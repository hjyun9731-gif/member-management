Railway 긴급복구 - 2026-10-07

증상:
ModuleNotFoundError: No module named 'psycopg'

원인:
Railway DATABASE_URL이 postgresql+psycopg:// 형식인데,
현재 이미지에는 psycopg2-binary가 설치되어 있어 SQLAlchemy가 psycopg(v3)를 찾다가 실패함.

적용:
이 ZIP의 app/database.py 한 파일만 GitHub main의 app/database.py에 덮어쓰기.
다른 파일은 건드리지 않음.

효과:
postgres://, postgresql://, postgresql+psycopg:// 를 모두
postgresql+psycopg2:// 로 정규화해서 기존 psycopg2-binary 드라이버 사용.

배포 후 Railway Deploy Logs에서 아래가 나오면 정상:
[railway-entry] real app ready ...
그리고 /health 200 확인.
