# 필요한 라이브러리들을 임포트합니다.
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
import boto3
import datetime
from flask_cors import CORS
from collections import Counter
from flask_sqlalchemy import SQLAlchemy
import requests
import os
import threading
from zoneinfo import ZoneInfo

# .env 파일에 정의된 환경 변수를 로드합니다.
# 이 코드는 app 객체 생성 전에 위치해야 합니다.
load_dotenv()

# Flask 애플리케이션 객체를 생성합니다.
app = Flask(__name__)
# 다른 도메인에서의 API 요청을 허용(CORS)합니다.
CORS(app)


# --- 데이터베이스 설정 ---
# SQLite 데이터베이스 파일의 경로를 지정합니다.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///gallery.db'
# SQLAlchemy의 이벤트를 처리하지 않도록 설정하여 오버헤드를 줄입니다.
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# SQLAlchemy 객체를 Flask 앱과 연결하여 초기화합니다.
db = SQLAlchemy(app)


# --- 데이터베이스 모델 정의 ---
class Gallery(db.Model):
    """
    업로드된 이미지의 메타데이터를 저장하기 위한 데이터베이스 테이블 모델입니다.
    """
    # 각 레코드를 식별하기 위한 고유 ID, 자동으로 증가합니다.
    id = db.Column(db.Integer, primary_key=True)
    # 이미지가 업로드된 시점의 타임스탬프 (UTC 기준)
    timestamp = db.Column(db.String(50), nullable=False)
    # S3에 저장된 이미지의 고유 URL, 중복될 수 없습니다.
    url = db.Column(db.String(200), unique=True, nullable=False)
    # 사용자가 작성한 메모
    memo = db.Column(db.Text, nullable=True)


# --- AWS S3 클라이언트 설정 ---
# boto3 라이브러리를 사용하여 S3 서비스와 통신하는 클라이언트를 생성합니다.
# EC2 IAM 역할을 사용하므로 별도의 자격 증명은 필요 없습니다.
s3 = boto3.client('s3')
# 이미지를 업로드할 S3 버킷의 이름을 지정합니다.
BUCKET_NAME = 'fall-detection-images'


# --- 헬퍼 함수 정의 ---
def send_sms_notification():
    """
    환경 변수에 지정된 수신자에게 SMS 알림을 전송합니다.
    이 함수는 백그라운드 스레드에서 실행됩니다.
    """
    # .env 파일에서 Textbelt API 키와 수신자 정보를 읽어옵니다.
    api_key = os.getenv('TEXTBELT_API_KEY')
    phone_number = os.getenv('RECIPIENT_PHONE_NUMBER')
    gallery_url = os.getenv('GALLERY_URL')

    # 환경 변수가 올바르게 설정되었는지 확인합니다.
    if not all([api_key, phone_number, gallery_url]):
        print("SMS 발송 실패: 환경 변수가 올바르게 설정되지 않았습니다.")
        return

    # 전송할 메시지 내용을 구성합니다.
    # Textbelt 키가 URL 전송을 허용하도록 인증되면 아래 주석을 해제하여 사용합니다.
    # message = f"낙상이 감지되었습니다! 즉시 아래 갤러리 링크를 확인하세요:\n{gallery_url}"
    message = "낙상이 감지되었습니다! 갤러리를 확인하세요."
    
    print(f"{phone_number}로 SMS 전송을 시도합니다...")
    try:
        # Textbelt API로 POST 요청을 보냅니다.
        response = requests.post('https://textbelt.com/text', {
            'phone': phone_number,
            'message': message,
            'key': api_key,
        })
        print(f"SMS API 응답: {response.json()}")
    except requests.exceptions.RequestException as e:
        print(f"SMS API 호출 중 오류 발생: {e}")


# --- API 엔드포인트 정의 ---
@app.route('/memo', methods=['POST'])
def save_memo():
    """
    특정 이미지에 대한 메모를 데이터베이스에 저장하거나 업데이트합니다.
    """
    data = request.get_json()
    image_url = data.get('url')
    memo = data.get('memo')

    # 이미지 URL을 기준으로 데이터베이스에서 해당 레코드를 찾습니다.
    item = Gallery.query.filter_by(url=image_url).first()
    if item:
        item.memo = memo
        db.session.commit() # 변경사항을 데이터베이스에 최종 반영합니다.
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'status': 'error', 'message': 'Image not found'}), 404

@app.route('/upload', methods=['POST'])
def upload_image():
    """
    낙상 감지기로부터 이미지를 받아 S3에 업로드하고,
    메타데이터를 DB에 저장한 후 SMS 알림을 보냅니다.
    """
    # 서버의 현재 시간(UTC)을 기준으로 타임스탬프를 생성합니다.
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    timestamp = utc_now.strftime('%Y-%m-%d_%H-%M-%S')
    
    file = request.files.get('image0')
    if file:
        filename = f"{timestamp}_fall_detection.jpg"
        s3_url = f"https://{BUCKET_NAME}.s3.amazonaws.com/{filename}"

        try:
            # 파일을 S3 버킷에 업로드합니다.
            s3.upload_fileobj(file, BUCKET_NAME, filename, ExtraArgs={'ContentType': 'image/jpeg'})
            
            # DB에 저장할 새 이미지 레코드를 생성합니다.
            new_image = Gallery(timestamp=timestamp, url=s3_url, memo='[자동 감지] 낙상 의심')
            db.session.add(new_image)
            db.session.commit()

            # SMS 전송 함수를 백그라운드 스레드에서 실행하여 응답 지연을 방지합니다.
            sms_thread = threading.Thread(target=send_sms_notification)
            sms_thread.start()

            return jsonify({'status': 'ok', 'url': s3_url}), 200

        except Exception as e:
            # 오류 발생 시 데이터베이스 변경사항을 되돌립니다.
            db.session.rollback()
            print(f"업로드 또는 DB 저장 실패: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

    return jsonify({'status': 'error', 'message': 'No image file found'}), 400

@app.route('/gallery')
def show_gallery():
    """
    데이터베이스에 저장된 모든 이미지의 목록을 JSON 형식으로 반환합니다.
    이때 타임스탬프는 KST(한국 시간)로 변환하여 제공합니다.
    """
    all_items = Gallery.query.order_by(Gallery.timestamp.desc()).all()
    
    utc_zone = ZoneInfo("UTC")
    kst_zone = ZoneInfo("Asia/Seoul")

    result = []
    for item in all_items:
        # DB에 저장된 UTC 시간 문자열을 파싱합니다.
        naive_time = datetime.datetime.strptime(item.timestamp, '%Y-%m-%d_%H-%M-%S')
        # 시간대 정보를 UTC로 명시한 후, KST로 변환합니다.
        utc_time = naive_time.replace(tzinfo=utc_zone)
        kst_time = utc_time.astimezone(kst_zone)
        # 프론트엔드에 표시할 형식으로 문자열을 포맷팅합니다.
        formatted_time = kst_time.strftime('%Y년 %m월 %d일 %H:%M:%S KST')

        result.append({
            'timestamp': item.timestamp,
            'url': item.url,
            'memo': item.memo,
            'formatted_timestamp': formatted_time
        })

    return jsonify(result)

@app.route('/stats/data')
def stats_data():
    """
    일별 이미지 업로드 통계 데이터를 JSON 형식으로 반환합니다.
    """
    all_items = Gallery.query.all()
    date_counts = Counter([item.timestamp.split('_')[0] for item in all_items])
    # 날짜순으로 정렬하여 반환합니다.
    sorted_counts = sorted(date_counts.items())
    return jsonify({
        'labels': [d for d, _ in sorted_counts],
        'counts': [c for _, c in sorted_counts]
    })


# --- 웹 페이지 렌더링 ---
@app.route('/')
def home():
    """
    메인 갤러리 웹 페이지(gallery.html)를 렌더링합니다.
    """
    return render_template('gallery.html')

@app.route('/stats')
def stats_page():
    """
    통계 웹 페이지(statistics.html)를 렌더링합니다.
    """
    return render_template('statistics.html')


# --- 애플리케이션 실행 ---
if __name__ == '__main__':
    # 애플리케이션 컨텍스트 안에서 데이터베이스와 테이블을 생성합니다.
    # 이는 서버가 시작될 때 DB 파일이나 테이블이 없으면 자동으로 생성해줍니다.
    with app.app_context():
        db.create_all()
        
    # Flask 개발 서버를 실행합니다.
    # host='0.0.0.0'은 모든 네트워크 인터페이스에서 접속을 허용합니다.
    app.run(host='0.0.0.0', port=5000)