"""CSV 내 스프레드시트 수식 실행 위험을 줄이는 순수 함수.

DB의 원본을 수정하지 않고 다운로드 텍스트에만 적용한다.
"""


def safe_csv_cell(value) -> str:
    s = "" if value is None else str(value)
    # 선행 공백/제어문자/제로폭 공백 뒤의 = + - @ 수식을 차단한다.
    if s.lstrip(" \t\r\n\ufeff\u200b").startswith(("=", "+", "-", "@")):
        return "'" + s
    return s
