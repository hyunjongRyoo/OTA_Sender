# OTA Sender

Windows PC에서 SoC의 APP 펌웨어 BIN 파일을 TCP로 전송하는 OTA 도구입니다.

이 저장소의 현재 버전은 현장 시연용으로 사용 중인 **단일 SoC 연결 방식**입니다. PC가 TCP 서버를 열고 SoC 한 대의 연결을 받은 뒤, 선택한 BIN 파일을 기존 OTA 프로토콜로 전송합니다.

## 현재 기능

- 로컬 PC IPv4 주소 선택 및 새로 고침
- TCP OTA 서버 시작/중지
- 연결된 SoC IP와 포트 표시
- APP 펌웨어 `.bin` 파일 선택
- 펌웨어 크기 및 CRC32 전송
- 청크 단위 펌웨어 전송과 진행률 표시
- TX/RX 데이터와 OTA 진행 로그 표시

현재 버전은 연결 소켓 하나만 관리하므로 여러 SoC를 동시에 연결하거나 선택하여 업데이트하지 않습니다.

## OTA 순서

프로그램은 다음 순서로 기존 OTA 명령을 전송합니다.

1. `FWU!` 전송 후 `FWU=READ` 확인
2. `FWH!`로 파일 크기와 CRC32 전송 후 `FWH=READ` 확인
3. `FWD!`로 BIN 데이터를 청크 단위 전송
4. 중간 청크의 `FWD=READ` 및 마지막 청크의 `FWD=SUCC` 확인

헤더의 파일 크기와 CRC32는 little-endian 32비트 값으로 전송합니다. 데이터 청크 크기는 244바이트입니다.

## 실행 방법

Python 3가 설치된 Windows PC에서 다음 명령으로 GUI를 실행합니다.

```powershell
python ota_send_gui.py
```

사용 순서:

1. `Local PC IP`에서 SoC가 접속할 서버 PC 주소를 선택합니다.
2. 포트를 확인합니다. 기본값은 `9001`입니다.
3. `Start Server`를 누릅니다.
4. SoC 연결 상태와 접속 IP를 확인합니다.
5. APP 펌웨어 BIN 파일을 선택합니다.
6. `Start Update`를 누릅니다.
7. 진행 로그에서 `FWD=SUCC`와 완료 메시지를 확인합니다.

현장 사용 시에는 한 번에 SoC 한 대만 서버에 연결하여 업데이트합니다.

## CLI 실행

```powershell
python ota_send.py firmware.bin --host 0.0.0.0 --port 9001
```

## Windows EXE 빌드

PyInstaller 설치 후 저장소 루트에서 빌드합니다.

```powershell
python -m pip install pyinstaller
pyinstaller --clean ota_send_gui.spec
pyinstaller --clean ota_send.spec
```

생성 파일:

- GUI: `dist/ota_send_gui.exe`
- CLI: `dist/ota_send.exe`

현장 배포본에서는 GUI 실행 파일을 `OTA_Sender.exe`라는 이름으로 사용합니다.

## 파일 구성

- `ota_send_gui.py`: Windows GUI 및 TCP 서버/연결 관리
- `ota_send.py`: OTA 프레임 처리와 BIN 전송 로직, CLI
- `ota_send_gui.spec`: GUI용 PyInstaller 빌드 설정
- `ota_send.spec`: CLI용 PyInstaller 빌드 설정

## 현재 버전 범위

- 단일 SoC 연결 및 업데이트만 지원
- 다중 기기 목록, 체크박스 선택 및 선택 기기 순차 업데이트는 아직 포함하지 않음
- OTA 명령, 패킷 형식, CRC32 및 청크 크기는 현재 현장용 구현을 유지
