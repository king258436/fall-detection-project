# gstreamer.py

# Copyright 2019 Google LLC
# ... (라이선스 헤더는 원본과 동일) ...

from gi.repository import GLib, GObject, Gst, GstBase, GstVideo, Gtk
import gi
import numpy as np
import sys
import threading

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')
gi.require_version('Gtk', '3.0')

Gst.init(None)

class GstPipeline:
    def __init__(self, pipeline, inf_callback, render_callback, src_size):
        self.inf_callback = inf_callback
        self.render_callback = render_callback
        self.running = False
        self.gstbuffer = None
        self.output = None  # 이제 (model_output, frame) 튜플을 저장
        self.sink_size = None
        self.src_size = src_size
        self.box = None
        self.condition = threading.Condition()

        self.pipeline = Gst.parse_launch(pipeline)
        self.freezer = self.pipeline.get_by_name('freezer')
        self.overlay = self.pipeline.get_by_name('overlay')
        self.overlaysink = self.pipeline.get_by_name('overlaysink')
        appsink = self.pipeline.get_by_name('appsink')
        appsink.connect('new-sample', self.on_new_sample)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self.on_bus_message)
        self.setup_window()

    def run(self):
        self.running = True
        inf_worker = threading.Thread(target=self.inference_loop)
        inf_worker.start()
        render_worker = threading.Thread(target=self.render_loop)
        render_worker.start()

        self.pipeline.set_state(Gst.State.PLAYING)
        self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
        
        # ... (run 함수의 나머지 부분은 원본과 동일) ...
        if self.overlaysink:
            sinkelement = self.overlaysink.get_by_interface(GstVideo.VideoOverlay)
        else:
            sinkelement = self.pipeline.get_by_interface(GstVideo.VideoOverlay)
        if sinkelement:
            sinkelement.set_property('sync', False)
            sinkelement.set_property('qos', False)

        try:
            Gtk.main()
        except:
            pass

        self.pipeline.set_state(Gst.State.NULL)
        while GLib.MainContext.default().iteration(False):
            pass
        with self.condition:
            self.running = False
            self.condition.notify_all()
        inf_worker.join()
        render_worker.join()

    def on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            Gtk.main_quit()
        elif t == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            sys.stderr.write('Warning: %s: %s\n' % (err, debug))
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            sys.stderr.write('Error: %s: %s\n' % (err, debug))
            Gtk.main_quit()
        return True

    def on_new_sample(self, sink):
        sample = sink.emit('pull-sample')
        if not self.sink_size:
            s = sample.get_caps().get_structure(0)
            self.sink_size = (s.get_value('width'), s.get_value('height'))
        with self.condition:
            self.gstbuffer = sample.get_buffer()
            self.condition.notify_all()
        return Gst.FlowReturn.OK

    def get_box(self):
        # ... (get_box 함수는 원본과 동일) ...
        if not self.box:
            glbox = self.pipeline.get_by_name('glbox')
            if glbox:
                glbox = glbox.get_by_name('filter')
            box = self.pipeline.get_by_name('box')
            assert glbox or box
            assert self.sink_size
            if glbox:
                self.box = (glbox.get_property('x'), glbox.get_property('y'),
                            glbox.get_property('width'), glbox.get_property('height'))
            else:
                self.box = (-box.get_property('left'), -box.get_property('top'),
                    self.sink_size[0] + box.get_property('left') + box.get_property('right'),
                    self.sink_size[1] + box.get_property('top') + box.get_property('bottom'))
        return self.box

# gstreamer.py 파일 안에 있는 inference_loop 함수만 아래 코드로 교체하세요.

    def inference_loop(self):
        while True:
            with self.condition:
                while not self.gstbuffer and self.running:
                    self.condition.wait()
                if not self.running:
                    break
                gstbuffer = self.gstbuffer
                self.gstbuffer = None

            # --- [수정된 코드 시작] ---
            # 스트라이드를 고려하여 프레임 데이터를 올바르게 추출
            meta = GstVideo.buffer_get_video_meta(gstbuffer)
            if not meta: continue
            
            result, mapinfo = gstbuffer.map(Gst.MapFlags.READ)
            if not result: continue

            height, width = meta.height, meta.width
            stride = meta.stride[0] # 실제 메모리의 한 줄 길이 (패딩 포함)
            
            # 전체 버퍼를 (높이 x 스트라이드) 2D 배열로 먼저 변환
            buffer_view = np.frombuffer(mapinfo.data, dtype=np.uint8).reshape((height, stride))
            
            # 각 줄에서 패딩을 제외한 실제 이미지 너비만큼만 잘라내어 새로운 배열 생성
            # 이 과정에서 메모리가 복사되며 패딩이 제거됨
            frame = buffer_view[:, :width * 3].reshape((height, width, 3))
            
            gstbuffer.unmap(mapinfo)
            # --- [수정된 코드 끝] ---

            # 추론 콜백 호출 (복사본 전달)
            output = self.inf_callback(frame.copy())
            
            with self.condition:
                # 렌더링 스레드에 추론 결과와 함께 방금 사용한 프레임을 같이 넘겨줌
                self.output = (output, frame) 
                self.condition.notify_all()

    def render_loop(self):
        while True:
            with self.condition:
                while not self.output and self.running:
                    self.condition.wait()
                if not self.running:
                    break
                # 추론 결과와 프레임을 한 번에 받음
                output, frame = self.output
                self.output = None

            # 렌더링 콜백 호출
            svg, freeze = self.render_callback(output, self.src_size, self.get_box(), frame)
            
            self.freezer.frozen = freeze
            if self.overlaysink:
                self.overlaysink.set_property('svg', svg)
            elif self.overlay:
                self.overlay.set_property('data', svg)

    # setup_window 함수 및 나머지 코드는 원본과 동일하게 유지
    def setup_window(self):
        # ... (setup_window 함수는 원본과 동일) ...
        if not self.overlaysink:
            return
        gi.require_version('GstGL', '1.0')
        from gi.repository import GstGL
        def on_gl_draw(sink, widget):
            widget.queue_draw()
        def on_widget_configure(widget, event, overlaysink):
            allocation = widget.get_allocation()
            overlaysink.set_render_rectangle(allocation.x, allocation.y,
                allocation.width, allocation.height)
            return False
        window = Gtk.Window(Gtk.WindowType.TOPLEVEL)
        window.fullscreen()
        drawing_area = Gtk.DrawingArea()
        window.add(drawing_area)
        drawing_area.realize()
        self.overlaysink.connect('drawn', on_gl_draw, drawing_area)
        wl_handle = self.overlaysink.get_wayland_window_handle(drawing_area)
        self.overlaysink.set_window_handle(wl_handle)
        wl_display = self.overlaysink.get_default_wayland_display_context()
        self.overlaysink.set_context(wl_display)
        drawing_area.connect('configure-event', on_widget_configure, self.overlaysink)
        window.connect('delete-event', Gtk.main_quit)
        window.show_all()
        def on_bus_message_sync(bus, message, overlaysink):
            if message.type == Gst.MessageType.NEED_CONTEXT:
                _, context_type = message.parse_context_type()
                if context_type == GstGL.GL_DISPLAY_CONTEXT_TYPE:
                    sinkelement = overlaysink.get_by_interface(GstVideo.VideoOverlay)
                    gl_context = sinkelement.get_property('context')
                    if gl_context:
                        display_context = Gst.Context.new(GstGL.GL_DISPLAY_CONTEXT_TYPE, True)
                        GstGL.context_set_gl_display(display_context, gl_context.get_display())
                        message.src.set_context(display_context)
            return Gst.BusSyncReply.PASS
        bus = self.pipeline.get_bus()
        bus.set_sync_handler(on_bus_message_sync, self.overlaysink)

# 나머지 코드 (run_pipeline, Freezer 클래스 등)는 원본과 동일하게 유지
# ... (이하 모든 코드는 제공해주신 원본 gstreamer.py와 동일합니다) ...
def on_bus_message(bus, message, loop):
    t = message.type
    if t == Gst.MessageType.EOS:
        loop.quit()
    elif t == Gst.MessageType.WARNING:
        err, debug = message.parse_warning()
        sys.stderr.write('Warning: %s: %s\n' % (err, debug))
    elif t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        sys.stderr.write('Error: %s: %s\n' % (err, debug))
        loop.quit()
    return True

def detectCoralDevBoard():
    try:
        if 'MX8MQ' in open('/sys/firmware/devicetree/base/model').read():
            print('Detected Edge TPU dev board.')
            return True
    except:
        pass
    return False

class Freezer(GstBase.BaseTransform):
    __gstmetadata__ = ('<longname>', '<class>', '<description>', '<author>')
    __gsttemplates__ = (Gst.PadTemplate.new('sink',
                                             Gst.PadDirection.SINK,
                                             Gst.PadPresence.ALWAYS,
                                             Gst.Caps.new_any()),
                                    Gst.PadTemplate.new('src',
                                             Gst.PadDirection.SRC,
                                             Gst.PadPresence.ALWAYS,
                                             Gst.Caps.new_any())
                                    )
    def __init__(self):
        self.buf = None
        self.frozen = False
        self.set_passthrough(False)
        super().__init__()
    def do_prepare_output_buffer(self, inbuf):
        if self.frozen:
            if not self.buf:
                self.buf = inbuf
            src_buf = self.buf
        else:
            src_buf = inbuf
        buf = Gst.Buffer.new()
        buf.copy_into(src_buf, Gst.BufferCopyFlags.FLAGS | Gst.BufferCopyFlags.TIMESTAMPS |
            Gst.BufferCopyFlags.META | Gst.BufferCopyFlags.MEMORY, 0, src_buf.get_size())
        buf.pts = inbuf.pts
        return (Gst.FlowReturn.OK, buf)

def register_elements(plugin):
    gtype = GObject.type_register(Freezer)
    Gst.Element.register(plugin, 'freezer', 0, gtype)
    return True

Gst.Plugin.register_static(
    Gst.version()[0], Gst.version()[1], # GStreamer version
    'freezer_plugin',                   # name
    'Video freezer',                    # description
    register_elements,                  # init_func
    '1.0',                              # version
    'unknown',                          # license
    'gstreamer-python',                 # source
    'gstreamer-python',                 # package
    'http://gstreamer.net/'             # origin
)

def run_pipeline(inf_callback, render_callback, src_size,
                 inference_size,
                 mirror=False,
                 h264=False,
                 jpeg=False,
                 videosrc='/dev/video0'):
    if h264:
        SRC_CAPS = 'video/x-h264,width={width},height={height},framerate=30/1'
    elif jpeg:
        SRC_CAPS = 'image/jpeg,width={width},height={height},framerate=30/1'
    else:
        SRC_CAPS = 'video/x-raw,width={width},height={height},framerate=30/1'
    PIPELINE = 'v4l2src device=%s ! {src_caps}' % videosrc

    scale = min(inference_size[0] / src_size[0],
                inference_size[1] / src_size[1])
    scale = tuple(int(x * scale) for x in src_size)
    scale_caps = 'video/x-raw,width={width},height={height}'.format(
        width=scale[0], height=scale[1])
    PIPELINE += """ ! decodebin ! videoflip video-direction={direction} ! tee name=t
            t. ! {leaky_q} ! videoconvert ! freezer name=freezer ! rsvgoverlay name=overlay
               ! videoconvert ! autovideosink
            t. ! {leaky_q} ! videoconvert ! videoscale ! {scale_caps} ! videobox name=box autocrop=true
               ! {sink_caps} ! {sink_element}
        """

    SINK_ELEMENT = 'appsink name=appsink emit-signals=true max-buffers=1 drop=true'
    SINK_CAPS = 'video/x-raw,format=RGB,width={width},height={height}'
    LEAKY_Q = 'queue max-size-buffers=1 leaky=downstream'
    direction = 'horiz' if mirror else 'identity'

    src_caps = SRC_CAPS.format(width=src_size[0], height=src_size[1])
    sink_caps = SINK_CAPS.format(width=inference_size[0], height=inference_size[1])
    pipeline = PIPELINE.format(src_caps=src_caps, sink_caps=sink_caps,
        sink_element=SINK_ELEMENT, direction=direction, leaky_q=LEAKY_Q, scale_caps=scale_caps)
    print('Gstreamer pipeline: ', pipeline)
    pipeline = GstPipeline(pipeline, inf_callback, render_callback, src_size)
    pipeline.run()
