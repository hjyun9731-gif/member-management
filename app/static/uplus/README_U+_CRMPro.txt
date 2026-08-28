LG U+ CRM Pro 연동 안내

1) CRM Pro Web Setup 1.2.15로 LG U+ CRM Pro를 먼저 설치합니다.
2) 이 폴더의 CRMProBridge.ps1과 U+_CRMPro_연동설치.bat를 같은 폴더에 둡니다.
3) U+_CRMPro_연동설치.bat를 한 번 실행합니다. 관리자 권한 확인창이 뜨면 허용합니다.
4) 회원관리 > 수납/미수금에서 회원 옆 'U+ 문자'를 누르고 '연결확인'을 누릅니다.
5) 연결되면 'U+로 전송' 버튼이 이 PC의 CRM Pro에 번호/내용을 전달합니다.

중요:
- Railway 서버가 문자를 보내는 구조가 아닙니다. 실제 전송은 직원 PC에 설치된 LG U+ CRM Pro가 담당합니다.
- CRM Pro 버전/화면 접근성 이름이 다르면 작성창은 열려도 자동입력이 안 될 수 있습니다. 이 경우 번호와 내용은 클립보드에 자동 복사됩니다.
- 자동입력 실패 시 로그: %LOCALAPPDATA%\MemberManagement\UPlusBridge\bridge.log
- 제거하려면 U+_CRMPro_연동제거.bat를 실행합니다.
