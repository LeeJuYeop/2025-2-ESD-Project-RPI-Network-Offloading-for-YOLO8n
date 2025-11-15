import cv2
import numpy as np
from flask import Flask, request, jsonify
from ultralytics import YOLO
import threading # 스레딩 라이브러리 임포트
import time
import argparse  # 1. argparse 라이브러리 임포트
import datetime  # 2. 파일명에 타임스탬프를 찍기 위해 임포트

# Flask 앱 초기화
app = Flask(__name__)

# YOLOv8n 모델 로드
print("YOLOv8n 모델을 로드 중입니다...")
model = YOLO('yolov8n.pt')
print("모델 로드 완료.")

# --- 스레드 간 통신을 위한 공유 변수 ---
latest_frame = None       # Flask 스레드가 처리한 최신 프레임을 저장
frame_lock = threading.Lock() # 위 변수를 안전하게 접근하기 위한 잠금장치
# --------------------------------------

@app.route('/detect', methods=['POST'])
def detect_object():
    global latest_frame, frame_lock # 전역 변수 사용 선언

    if 'image' not in request.files:
        return jsonify({'error': '이미지 파일이 없습니다.'}), 400

    file = request.files['image']
    image_bytes = file.read()
    np_arr = np.frombuffer(image_bytes, np.uint8)
    
    try:
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("디코딩된 프레임이 비어 있습니다.")
    except Exception as e:
        return jsonify({'error': f'이미지 디코딩 실패: {e}'}), 400

    # --- YOLO 추론 ---
    results = model(frame, verbose=False) 
    
    response_data = {'x': None, 'y': None}
    annotated_frame = frame.copy() # 원본 프레임 복사

    # 결과에서 바운딩 박스 정보 추출 및 프레임에 그리기
    if len(results) > 0 and len(results[0].boxes) > 0:
        # RPI로 보낼 첫 번째 객체의 중심 좌표 찾기
        first_box = results[0].boxes[0]
        x1_f, y1_f, x2_f, y2_f = map(int, first_box.xyxy[0])
        center_x = int((x1_f + x2_f) / 2)
        center_y = int((y1_f + y2_f) / 2)
        response_data = {'x': center_x, 'y': center_y}

        # 시각화를 위해 *모든* 감지된 객체 그리기
        for box in results[0].boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            conf = float(box.conf[0])
            cls = int(box.cls[0])
            class_name = model.names[cls] if model.names else str(cls)
            
            # 바운딩 박스
            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            # 텍스트
            text = f"{class_name} {conf:.2f}"
            cv2.putText(annotated_frame, text, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        
        # RPI로 보낼 객체의 중심점만 특별히 그리기 (빨간색)
        cv2.circle(annotated_frame, (center_x, center_y), 5, (0, 0, 255), -1)

    # --- 메인 스레드로 프레임 전달 ---
    # Lock을 사용해 공유 변수(latest_frame)를 안전하게 업데이트
    with frame_lock:
        global latest_frame
        latest_frame = annotated_frame.copy()
    # -----------------------------------

    # RPI로는 좌표만 반환 (cv2.imshow 제거!)
    return jsonify(response_data)

def run_flask():
    """백그라운드 스레드에서 Flask 서버를 실행하는 함수"""
    print("Flask 서버를 백그라운드 스레드에서 시작합니다...")
    # 'use_reloader=False'는 스레드 환경에서 필수입니다.
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# --- 여기가 메인 스레드 ---
if __name__ == '__main__':
    # --- 1. Argument Parser 설정 ---
    parser = argparse.ArgumentParser(description="YOLO Detection Server")
    parser.add_argument(
        '--save',
        action='store_true',  # --save 옵션이 있으면 True가 됨
        help="감지된 비디오를 .avi 파일로 저장합니다."
    )
    args = parser.parse_args()
    save_video = args.save # True 또는 False
    # -----------------------------------

    # 2. Flask 서버를 백그라운드 스레드로 시작
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True 
    flask_thread.start()

    print("메인 스레드에서 OpenCV 디스플레이 루프를 시작합니다.")
    print("'q' 키를 누르면 종료됩니다.")
    
    cv2.namedWindow("YOLOv8 Detection from RPI", cv2.WINDOW_NORMAL)
    
    # --- 3. VideoWriter 변수 초기화 ---
    video_writer = None
    save_filename = ""
    # 저장할 비디오의 고정 FPS 설정 (RPI 전송 속도에 맞춰 조절 가능)
    DEFAULT_SAVE_FPS = 15.0 

    if save_video:
        # 파일명에 현재 날짜와 시간을 포함시켜 겹치지 않게 함
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        save_filename = f"output_{timestamp}.avi"
        print(f"[저장 활성화] 비디오가 {save_filename} (으)로 저장됩니다.")
    # -----------------------------------

    # --- FPS 계산용 변수 (기존과 동일) ---
    frame_counter = 0
    start_time_for_fps = time.time()
    displayed_fps = 0.0
    total_frame_count = 0
    program_start_time = time.time()
    # ----------------------------------

    # 4. 메인 스레드 GUI 루프
    while True:
        frame_to_show = None
        
        with frame_lock:
            if latest_frame is not None:
                frame_to_show = latest_frame.copy()
        
        if frame_to_show is not None:
            # --- 5. (신규) 첫 프레임 수신 시 VideoWriter 초기화 ---
            # 해상도를 알아야 초기화할 수 있으므로, 첫 프레임 수신 시 1회 실행
            if save_video and video_writer is None:
                h, w, _ = frame_to_show.shape
                fourcc = cv2.VideoWriter_fourcc(*'XVID') # .avi 코덱
                video_writer = cv2.VideoWriter(save_filename, fourcc, DEFAULT_SAVE_FPS, (w, h))
                print(f"VideoWriter 초기화 완료. {w}x{h} @ {DEFAULT_SAVE_FPS}FPS로 저장 시작.")
            # ----------------------------------------------------

            # FPS 계산 및 표시 (기존과 동일)
            frame_counter += 1
            total_frame_count += 1
            current_time = time.time()
            elapsed_time = current_time - start_time_for_fps
            if elapsed_time >= 1.0:
                displayed_fps = frame_counter / elapsed_time
                frame_counter = 0
                start_time_for_fps = current_time
            
            fps_text = f"FPS: {displayed_fps:.1f}"
            cv2.putText(frame_to_show, fps_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2, cv2.LINE_AA)
            
            # --- 6. (신규) 프레임 저장 ---
            if video_writer is not None:
                video_writer.write(frame_to_show) # 화면에 표시할 프레임을 파일에 씀
            # -----------------------------

            cv2.imshow("YOLOv8 Detection from RPI", frame_to_show)
        else:
            # 대기 화면 (기존과 동일)
            placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(placeholder, "Waiting for RPi...", (100, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            cv2.imshow("YOLOv8 Detection from RPI", placeholder)

        # 종료 키 (기존과 동일)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
            
    # --- 7. (신규) 프로그램 종료 시 비디오 파일 닫기 ---
    if video_writer is not None:
        video_writer.release() # 비디오 파일 저장 완료
        print(f"\n[저장 완료] 비디오 파일이 {save_filename} 에 저장되었습니다.")
    # --------------------------------------------------
            
    cv2.destroyAllWindows()
    
    # 평균 FPS 출력 (기존과 동일)
    total_elapsed_time = time.time() - program_start_time
    if total_elapsed_time > 0: 
        average_fps = total_frame_count / total_elapsed_time
        print("\n" + "="*30)
        print(f"프로그램이 종료되었습니다.")
        print(f"  총 실행 시간: {total_elapsed_time:.2f} 초")
        print(f"  처리한 총 프레임: {total_frame_count} 개")
        print(f"  [전체 평균 FPS: {average_fps:.2f}]")
        print("="*30)
    else:
        print("프로그램을 종료합니다.")