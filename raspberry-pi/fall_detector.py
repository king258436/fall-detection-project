import argparse
import collections
from functools import partial
import time
import svgwrite
import cv2
from datetime import datetime
import os
import threading
import queue
import requests

# 같은 폴더에 있는 gstreamer.py와 pose_engine.py를 임포트합니다.
import gstreamer
from pose_engine import PoseEngine
from pose_engine import KeypointType

# Posenet 모델의 스켈레톤에서 연결할 주요 신체 부위(엣지)를 정의합니다.
EDGES = (
    (KeypointType.NOSE, KeypointType.LEFT_SHOULDER),
    (KeypointType.NOSE, KeypointType.RIGHT_SHOULDER),
    (KeypointType.LEFT_SHOULDER, KeypointType.RIGHT_SHOULDER),
)

def shadow_text(dwg, x, y, text, font_size=16):
    """SVG 캔버스에 그림자가 있는 텍스트를 추가합니다."""
    dwg.add(dwg.text(text, insert=(x + 1, y + 1), fill='black',
                     font_size=font_size, style='font-family:sans-serif'))
    dwg.add(dwg.text(text, insert=(x, y), fill='white',
                     font_size=font_size, style='font-family:sans-serif'))

def draw_pose(dwg, pose, src_size, inference_box, color='yellow', threshold=0.2):
    """추론된 포즈의 스켈레톤을 SVG 캔버스에 그립니다."""
    box_x, box_y, box_w, box_h = inference_box
    scale_x, scale_y = src_size[0] / box_w, src_size[1] / box_h
    xys = {}
    for label, keypoint in pose.keypoints.items():
        if keypoint.score < threshold:
            continue
        kp_x = int((keypoint.point[0] - box_x) * scale_x)
        kp_y = int((keypoint.point[1] - box_y) * scale_y)
        xys[label] = (kp_x, kp_y)
        dwg.add(dwg.circle(center=(kp_x, kp_y), r=5,
                           fill='none', stroke='none', display='none'))

    for a, b in EDGES:
        if a not in xys or b not in xys:
            continue
        ax, ay = xys[a]
        bx, by = xys[b]
        dwg.add(dwg.line(start=(ax, ay), end=(bx, by), stroke=color, stroke_width=2))

def avg_fps_counter(window_size):
    """프레임 처리 속도(FPS)의 이동 평균을 계산합니다."""
    window = collections.deque(maxlen=window_size)
    prev = time.monotonic()
    yield 0.0
    while True:
        curr = time.monotonic()
        window.append(curr - prev)
        prev = curr
        yield len(window) / sum(window)

def run(inf_callback, render_callback):
    """
    명령어 라인 인자를 파싱하고, PoseEngine을 초기화한 후,
    GStreamer 파이프라인을 실행합니다.
    """
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--mirror', help='수평으로 비디오를 뒤집습니다.', action='store_true')
    parser.add_argument('--model', help='.tflite 모델 파일 경로', required=False)
    parser.add_argument('--res', help='해상도', default='640x480',
                        choices=['480x360', '640x480', '1280x720'])
    parser.add_argument('--videosrc', help='사용할 비디오 소스', default='/dev/video0')
    parser.add_argument('--h264', help='video/x-h264 입력을 사용합니다.', action='store_true')
    parser.add_argument('--jpeg', help='image/jpeg 입력을 사용합니다.', action='store_true')
    args = parser.parse_args()

    default_model = 'models/mobilenet/posenet_mobilenet_v1_075_%d_%d_quant_decoder_edgetpu.tflite'
    if args.res == '480x360':
        src_size = (640, 480)
        appsink_size = (480, 360)
        model = args.model or default_model % (353, 481)
    elif args.res == '640x480':
        src_size = (640, 480)
        appsink_size = (640, 480)
        model = args.model or default_model % (481, 641)
    elif args.res == '1280x720':
        src_size = (1280, 720)
        appsink_size = (1280, 720)
        model = args.model or default_model % (721, 1281)

    print('모델 로딩 중: ', model)
    engine = PoseEngine(model)
    input_shape = engine.get_input_tensor_shape()
    inference_size = (input_shape[2], input_shape[1])

    gstreamer.run_pipeline(partial(inf_callback, engine),
                           partial(render_callback, engine),
                           src_size, inference_size,
                           mirror=args.mirror,
                           videosrc=args.videosrc,
                           h264=args.h264,
                           jpeg=args.jpeg)

def main():
    """
    애플리케이션의 메인 로직을 설정하고 실행합니다.
    낙상 감지, 이미지 전송, 화면 오버레이를 모두 관리합니다.
    """
    # --- 애플리케이션 설정 및 상태 변수 ---
    SERVER_URL = 'http://44.201.150.94:5000/upload'
    n = 0
    sum_process_time = 0
    sum_inference_time = 0
    fps_counter = avg_fps_counter(30)

    # --- 낙상 감지 로직 관련 변수 ---
    # 최근 10 프레임 동안의 어깨 중심 Y좌표를 저장합니다.
    shoulder_y_history = collections.deque(maxlen=10)
    # Y좌표의 변화량이 이 값을 넘으면 낙상으로 판단합니다. (환경에 맞게 조절 필요)
    FALL_THRESHOLD = 50
    # 마지막으로 낙상이 감지된 시간을 기록하여 중복 감지를 방지합니다.
    fall_detected_time = 0
    # 감지 후 다음 감지까지의 최소 시간 간격(초)입니다.
    FALL_COOLDOWN_SECONDS = 5.0

    # --- 이미지 비동기 전송을 위한 큐 ---
    save_queue = queue.Queue()

    def image_save_worker():
        """
        백그라운드 스레드에서 실행되며, 큐에 들어온 이미지 프레임을 서버로 전송합니다.
        이를 통해 메인 스레드(영상 처리)의 지연을 방지합니다.
        """
        nonlocal save_queue
        while True:
            try:
                frame_to_send = save_queue.get(timeout=1)

                # OpenCV 프레임(Numpy 배열)을 JPEG 형식으로 메모리에서 인코딩합니다.
                is_success, buffer = cv2.imencode(".jpg", frame_to_send)
                if not is_success:
                    print("이미지 인코딩 실패")
                    continue

                # HTTP POST 요청을 위한 파일 데이터를 준비합니다.
                files = {'image0': ('fall_capture.jpg', buffer.tobytes(), 'image/jpeg')}

                # 서버로 이미지 데이터를 전송합니다 (10초 타임아웃).
                try:
                    response = requests.post(SERVER_URL, files=files, timeout=10)
                    if response.status_code == 200:
                        print(f"서버에 이미지 전송 성공: {response.json()}")
                    else:
                        print(f"서버에 이미지 전송 실패: 상태 코드 {response.status_code}")
                except requests.exceptions.RequestException as e:
                    print(f"서버 연결 오류: {e}")

                save_queue.task_done()
            except queue.Empty:
                continue

    # 이미지 전송 워커를 데몬 스레드로 시작합니다.
    threading.Thread(target=image_save_worker, daemon=True).start()

    # --- GStreamer 콜백 함수 정의 ---
    def run_inference(engine, input_tensor):
        """PoseEngine을 통해 모델 추론을 실행합니다."""
        return engine.run_inference(input_tensor.flatten())

    def render_overlay(engine, output, src_size, inference_box, frame):
        """
        매 프레임마다 호출되어, 추론 결과를 분석하고 화면에 오버레이를 렌더링합니다.
        """
        nonlocal n, sum_process_time, sum_inference_time, fps_counter
        nonlocal shoulder_y_history, fall_detected_time, save_queue

        svg_canvas = svgwrite.Drawing('', size=src_size)
        start_time = time.monotonic()
        outputs, inference_time = engine.ParseOutput()
        end_time = time.monotonic()

        # 성능 통계 계산 및 화면 표시
        n += 1
        sum_process_time += 1000 * (end_time - start_time)
        sum_inference_time += inference_time * 1000
        avg_inference_time = sum_inference_time / n if n > 0 else 0
        text_line = 'PoseNet: %.1fms (%.2f fps) TrueFPS: %.2f Nposes %d' % (
                     avg_inference_time, 1000 / avg_inference_time if avg_inference_time > 0 else 0,
                     next(fps_counter), len(outputs))
        shadow_text(svg_canvas, 10, 20, text_line)

        # 각 프레임에서 감지된 포즈들을 분석합니다.
        fall_detected_in_frame = False
        for pose in outputs:
            draw_pose(svg_canvas, pose, src_size, inference_box)
            ls = pose.keypoints.get(KeypointType.LEFT_SHOULDER)
            rs = pose.keypoints.get(KeypointType.RIGHT_SHOULDER)

            # 양쪽 어깨가 모두 감지되었을 경우, 낙상 감지 로직을 수행합니다.
            if ls and rs and ls.score > 0.5 and rs.score > 0.5:
                box_x, box_y, box_w, box_h = inference_box
                scale_y = src_size[1] / box_h
                ls_y = (ls.point[1] - box_y) * scale_y
                rs_y = (rs.point[1] - box_y) * scale_y
                shoulder_y = (ls_y + rs_y) / 2
                shoulder_y_history.append(shoulder_y)

                # 저장된 Y좌표 기록을 바탕으로 급격한 수직 하강이 있었는지 확인합니다.
                if len(shoulder_y_history) == shoulder_y_history.maxlen:
                    delta = shoulder_y_history[-1] - shoulder_y_history[0]
                    if delta > FALL_THRESHOLD:
                        fall_detected_in_frame = True

        # 낙상이 감지되었고, 쿨다운 시간이 지났다면 알림을 처리합니다.
        current_time = time.monotonic()
        if fall_detected_in_frame and (current_time - fall_detected_time > FALL_COOLDOWN_SECONDS):
            fall_detected_time = current_time
            shadow_text(svg_canvas, 10, 50, "넘어짐 감지!", font_size=24)

            # 이미지 전송 큐에 현재 프레임을 추가합니다.
            if not save_queue.full():
                save_queue.put(frame.copy())

        return (svg_canvas.tostring(), False)

    try:
        # 설정된 콜백 함수들을 GStreamer 파이프라인에 전달하여 실행합니다.
        run(run_inference, render_overlay)
    except KeyboardInterrupt:
        # Ctrl+C 입력 시 프로그램을 안전하게 종료합니다.
        print("\n프로그램 종료.")

# 이 스크립트가 직접 실행될 때 main 함수를 호출합니다.
if __name__ == '__main__':
    main()
