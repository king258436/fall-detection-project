# 실시간 낙상 감지 및 알림 시스템 (Real-time Fall Detection & Notification System)

![Python](https://img.shields.io/badge/python-3.9+-blue.svg)
![Framework](https://img.shields.io/badge/Flask-2.0-lightgrey.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

*저전력 엣지 디바이스에서 실시간으로 사용자의 낙상을 감지하고, 클라우드 서버를 통해 보호자에게 즉시 SMS 알림과 웹 기반 모니터링 환경을 제공하는 오픈소스 프로젝트입니다.*

<br>

![프로젝트 데모 GIF](https://example.com/demo.gif)
<br>

## 📖 목차
1. [프로젝트 소개](#-프로젝트-소개)
2. [주요 기능](#-주요-기능)
3. [시스템-구성도](#-시스템-구성도)
4. [기술-스택](#-기술-스택)
5. [시작하기](#-시작하기)
    - [사전 준비물](#사전-준비물)
    - [설치-및-실행](#설치-및-실행)
6. [기여하기](#-기여하기)
7. [라이선스](#-라이선스)

<br>

## 🎯 프로젝트 소개

1인 가구 및 독거노인 인구가 증가함에 따라, 실내에서 발생하는 낙상과 같은 위급 상황에 대한 신속한 대응의 중요성이 커지고 있습니다. 이 프로젝트는 **골든타임 확보**를 목표로, 딥러닝 자세 추정 모델을 활용하여 사용자의 상태를 24시간 모니터링하고, 낙상으로 판단되는 이상 상황 발생 시 수 초 내에 보호자에게 알림을 전송하여 신속한 대응을 가능하게 합니다.

<br>

## ✨ 주요 기능

* **👀 실시간 낙상 감지:** 라즈베리파이와 Google Coral TPU를 이용해 저전력 환경에서 24시간 실시간으로 사용자의 자세를 분석하고 낙상 이벤트를 감지합니다.
* **📲 즉각적인 SMS 알림:** 낙상 감지 즉시, 보호자의 스마트폰으로 경고 메시지와 현장 확인이 가능한 웹 갤러리 링크를 SMS로 발송합니다.
* **- 갤러리 및 기록 관리:** 감지된 모든 낙상 이벤트는 이미지와 시간 정보(KST)와 함께 웹 갤러리에 자동으로 기록되며, 보호자는 언제 어디서든 과거 기록을 확인하고 메모를 남길 수 있습니다.
* **📊 데이터 시각화:** 일별/주별 낙상 발생 빈도를 차트로 시각화하여 제공함으로써, 사용자의 상태 변화 패턴을 쉽게 파악할 수 있도록 돕습니다.

<br>

## 🗺️ 시스템 구성도
<img width="605" alt="스크린샷 2025-06-11 오후 5 03 03" src="https://github.com/user-attachments/assets/068c5371-44c9-4d1d-8626-ad57eff8fe53" />

<br>

## 🛠️ 기술 스택

### 엣지 디바이스 (`raspberry-pi/`)
* **Hardware:** Raspberry Pi 4, Google Coral USB Accelerator
* **Language:** Python
* **Libraries:** GStreamer, OpenCV, TFLite Runtime, Requests

### 클라우드 백엔드 (`server/`)
* **Cloud:** AWS EC2, AWS S3
* **Language:** Python
* **Framework & Libraries:** Flask, SQLAlchemy, Boto3, python-dotenv
* **Database:** SQLite
* **Notification:** Textbelt API

<br>

## 🚀 시작하기

이 프로젝트를 직접 실행해보기 위한 안내입니다.

### 사전 준비물
* Python 3.8+
* Git
* 라즈베리파이 4 및 Google Coral USB Accelerator
* AWS 계정 (EC2, S3 사용)
* Textbelt 계정 (API 키)

### 설치 및 실행

#### 1. 서버 (AWS EC2)
```bash
# 1. 저장소를 클론합니다.
git clone [https://github.com/king258436/fall-detection-project.git](https://github.com/king258436/fall-detection-project.git)
cd fall-detection-project/server

# 2. Python 가상환경을 생성하고 활성화합니다.
python3 -m venv venv
source venv/bin/activate

# 3. 필요한 라이브러리를 설치합니다.
pip install -r requirements.txt

# 4. .env 파일을 설정합니다. (.env.example 파일을 복사하여 만듭니다.)
# cp .env.example .env
# nano .env 
# -> 파일 안의 내용을 자신의 값으로 채워주세요.

# 5. Flask 서버를 실행합니다.
python3 server.py
```

#### 2. 클라이언트 (라즈베리파이)
```bash
# 1. 저장소를 클론합니다.
git clone [https://github.com/king258436/fall-detection-project.git](https://github.com/king258436/fall-detection-project.git)
cd fall-detection-project/raspberry-pi

# 2. Python 가상환경을 활성화하고 라이브러리를 설치합니다.
# (기존에 설정한 가상환경을 활성화하세요)
pip install -r requirements.txt

# 3. fall_detector.py 코드 상단의 SERVER_URL을 EC2 서버 주소로 맞게 수정합니다.

# 4. 낙상 감지 프로그램을 실행합니다.
python3 fall_detector.py
```

**`.env` 파일 설정 예시 (`server/.env.example`):**
> 이 예시 파일을 `server` 폴더에 `.env`로 복사하여 사용하세요.
```env
# Textbelt SMS 발송을 위한 API 키
TEXTBELT_API_KEY='Your_Textbelt_API_Key'

# SMS를 수신할 전화번호 (국가번호 포함)
RECIPIENT_PHONE_NUMBER='+821012345678'

# SMS 메시지에 포함될 갤러리 웹 페이지의 전체 주소
GALLERY_URL='http://Your_EC2_IP:5000'
```

<br>

## 🤝 기여하기

이 프로젝트에 기여하고 싶으시다면 언제든지 환영합니다! 기여는 이 오픈소스 커뮤니티를 더욱 멋진 곳으로 만드는 원동력입니다.

1. 프로젝트를 Fork 하세요.
2. 새로운 기능 브랜치를 만드세요. (`git checkout -b feature/AmazingFeature`)
3. 변경사항을 커밋하세요. (`git commit -m 'Add some AmazingFeature'`)
4. 브랜치에 푸시하세요. (`git push origin feature/AmazingFeature`)
5. Pull Request를 열어주세요.

<br>

## 📜 라이선스

이 프로젝트는 MIT 라이선스를 따릅니다. 자세한 내용은 `LICENSE` 파일을 참고해주세요.
(저장소에 MIT 라이선스 파일을 추가하는 것을 권장합니다.)

<br>
