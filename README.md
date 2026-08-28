# OTA Sender

Windows PC에서 SoC의 APP 펌웨어 BIN 파일을 TCP로 전송하는 OTA 도구입니다.

GUI는 PC에서 TCP 서버를 열고 접속한 여러 SoC를 IP 목록으로 표시한 뒤, 선택한 기기에 기존 OTA 프로토콜로 순차 전송합니다.

## 현재 기능

- 로컬 PC IPv4 주소 선택 및 새로 고침
- TCP 서버에 접속한 SoC IP와 포트 표시
- 기기 선택 상태를 `☐` / `☑`로 표시
- 실행 중 기기별 OTA 상태와 최근 성공/최종 실패 시간 표시
- APP 펌웨어 `.bin` 파일 선택
- 펌웨어 크기 및 CRC32 전송
- 청크 단위 펌웨어 전송과 진행률 표시
- TX/RX 데이터와 OTA 진행 로그 표시

현재 버전은 여러 SoC 연결을 IP별 목록으로 표시하고 선택한 기기를 순차 업데이트합니다.
기기 OTA가 실패하면 5초 대기 후 해당 기기에 한 번 다시 전송합니다. 재시도도
실패하면 해당 기기를 OTA 실패로 표시하고 이후 기기 처리를 중단합니다.

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
2. 포트를 확인합니다. 기본값은 TCP `9001`입니다.
3. `Start Server`를 누른 뒤 접속한 SoC 목록에서 업데이트할 기기를 선택합니다.
4. APP 펌웨어 BIN 파일을 선택합니다.
5. `Start Update`를 누릅니다. 선택된 기기를 순차 업데이트합니다.
6. 진행 로그에서 `FWD=SUCC`와 완료 메시지를 확인합니다.

기기별 첫 OTA가 실패하면 5초 후 한 번 다시 전송합니다. 재시도도
실패하면 해당 기기를 `OTA Failed`로 표시하고 이후 기기 처리를 중단합니다.
최근 업데이트 시간은 현재 프로그램 실행 중인 목록에서만 유지됩니다.

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

- `ota_send_gui.py`: Windows GUI 및 다중 TCP OTA 연결 관리
- `ota_send.py`: OTA 프레임 처리와 BIN 전송 로직, CLI
- `ota_send_gui.spec`: GUI용 PyInstaller 빌드 설정
- `ota_send.spec`: CLI용 PyInstaller 빌드 설정

## 현재 버전 범위

- 다중 기기 목록, 대상 선택 및 선택 기기 순차 업데이트 지원
- PC TCP 서버 기본 포트는 9001 사용
- 실패 시 5초 대기 후 1회 재시도, 재시도 실패 시 전체 처리 중단
- OTA 명령, 패킷 형식, CRC32 및 청크 크기는 현재 현장용 구현을 유지
