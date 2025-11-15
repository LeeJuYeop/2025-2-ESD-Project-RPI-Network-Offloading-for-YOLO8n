import cv2
import requests
import time
from picamera2 import Picamera2
import argparse  # 1. argparse 라이브러리 임포트

# --- 노트북 서버 설정 ---
# !!! [노트북_IP_주소] 부분은 실제 IP로 설정되어 있어야 합니다.
SERVER_URL = "http://192.168.2.1:5000/detect" 

# 2. 명령줄 인자 파서 설정
# ----------------------------------------------------
parser = argparse.ArgumentParser(description="RPI to Server Object Detection Client")
parser.add_argument(
    '--width', 
    type=int, 
    default=640,  # 사용자가 값을 입력하지 않을 경우 기본값
    help="카메라 캡처 가로 해상도 (기본값: 640)"
)
parser.add_argument(
    '--height', 
    type=int, 
    default=480,  # 사용자가 값을 입력하지 않을 경우 기본값
    help="카메라 캡처 세로 해상도 (기본값: 480)"
)
args = parser.parse_args() # 스크립트 실행 시 입력된 인자를 파싱
# ----------------------------------------------------


# PiCamera2 초기화
picam2 = Picamera2()

# 3. 인자로 받은 해상도로 카메라 설정
# ----------------------------------------------------
# 하드코딩된 (640, 480) 대신 args.width, args.height 사용
config = picam2.create_preview_configuration(
    main={"size": (args.width, args.height)}
)
# ----------------------------------------------------

picam2.configure(config)
picam2.start()

# PiCamera가 사용자가 요청한 해상도를 지원하지 않을 경우,
# 가장 근접한 해상도를 선택할 수 있습니다.
# 따라서 실제 설정된 해상도 값을 확인차 출력해줍니다.
print(f"요청한 해상도: {args.width}x{args.height}")
print(f"실제 설정된 해상도: {picam2.camera_configuration()['main']['size']}")

print("카메라 준비 완료. 2초 후 시작합니다...")
time.sleep(2.0) 
try:
    while True:
        # 1. RPI에서 프레임 캡처 (결과물: RGB)
        frame_rgb = picam2.capture_array()
        
        # 2. [!!! 해결책 !!!]
        # picamera2의 'RGB' 이미지를 OpenCV의 'BGR' 이미지로 변환
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        
        # 3. 'BGR' 이미지를 JPEG로 압축
        is_success, buffer = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not is_success:
            print("프레임 압축 실패")
            continue
            
        # 4. HTTP POST 요청으로 서버에 이미지 전송
        files = {'image': ('frame.jpg', buffer.tobytes(), 'image/jpeg')}
        
        try:
            # ( ... 나머지 서버 요청 및 응답 처리 코드는 동일 ... )
            response = requests.post(SERVER_URL, files=files, timeout=1.0)
            
            if response.status_code == 200:
                data = response.json()
                x = data.get('x')
                y = data.get('y')
                if x is not None and y is not None:
                    print(f"객체 감지: ({x}, {y})")
                else:
                    print("객체 감지되지 않음")
            else:
                print(f"서버 에러: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            print(f"서버 연결 실패: {e}")
            time.sleep(1)

except KeyboardInterrupt:
    print("프로그램 종료 중...")
finally:
    picam2.stop()
    print("카메라 종료.")