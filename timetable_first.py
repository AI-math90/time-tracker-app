"""
데일리 시간 관리 앱 (Time Tracker)
- 데이터 저장소: Google Sheets (streamlit-gsheets-connection)
- 워크시트: goals, timetable, cumulative, day_type
"""
import streamlit as st
import pandas as pd
import time
from datetime import date, datetime, timedelta

from streamlit_gsheets import GSheetsConnection

try:
    from gspread.exceptions import APIError as GSpreadAPIError
except ImportError:
    GSpreadAPIError = Exception  # fallback if gspread not installed

# =============================================================================
# [구글 시트 연동] st.connection으로 secrets.toml의 connections.gsheets 사용
# Service Account 인증 시 해당 스프레드시트를 서비스 계정 이메일과 공유해야 함
# =============================================================================
@st.cache_resource
def _get_gsheets_conn():
    """연결 인스턴스를 캐시하여 매 요청마다 재생성하지 않음."""
    return st.connection("gsheets", type=GSheetsConnection)

# [F-04] 출근일 시간대: 06-09, 12-13, 18-23 / 휴일: 06-23
BUSINESS_HOURS = ["06:00", "07:00", "08:00", "09:00", "12:00", "13:00", "18:00", "19:00", "20:00", "21:00", "22:00", "23:00"]
HOLIDAY_HOURS = [f"{str(h).zfill(2)}:00" for h in range(6, 24)]

# 구글 시트 내 워크시트(탭) 이름 — 스프레드시트에 동일한 이름의 시트가 있어야 함
WORKSHEET_GOALS = "goals"
WORKSHEET_TIMETABLE = "timetable"
WORKSHEET_CUMULATIVE = "cumulative"
WORKSHEET_DAY_TYPE = "day_type"


def _str_or_blank(val):
    """NaN 또는 'nan' 문자열을 공란으로 통일해 UI 표기 문제 방지."""
    if pd.isna(val):
        return ""
    s = str(val).strip()
    return "" if s == "nan" else s


def _to_bool(val):
    """구글 시트 등에서 읽은 값을 불리언으로 변환 (기본 False)."""
    if pd.isna(val):
        return False
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in ("true", "1", "yes", "o")


def _normalize_date_col(df: pd.DataFrame, col: str = "Date") -> pd.DataFrame:
    """Date 컬럼을 YYYY-MM-DD 문자열로 통일해 날짜 변경 시 비교/필터 오류 방지."""
    if df.empty or col not in df.columns:
        return df
    def _norm(v):
        if pd.isna(v):
            return ""
        try:
            return pd.to_datetime(v).strftime("%Y-%m-%d")
        except Exception:
            return str(v).strip().split()[0][:10] if str(v).strip() else ""
    df = df.copy()
    df[col] = df[col].apply(_norm)
    return df


def get_slots(day_type: str, use_30min: bool) -> list:
    """날짜 유형과 30분 단위 사용 여부에 따라 시간 슬롯 리스트 반환."""
    base = BUSINESS_HOURS if day_type == "business" else HOLIDAY_HOURS
    if not use_30min:
        return base.copy()
    slots = []
    for t in base:
        h = int(t.split(":")[0])
        slots.append(f"{h:02d}:00")
        slots.append(f"{h:02d}:30")
    return slots


# =============================================================================
# [구글 시트 API 기반 데이터 로드/저장]
# - load_data: 해당 워크시트 전체를 Select(읽기). ttl=0으로 캐시 비활성화해 항상 최신 데이터 반영
# - save_data: 해당 워크시트 전체를 Update(덮어쓰기). 기존 시트 내용을 지우고 DataFrame 전체 기록
# =============================================================================
def load_data(worksheet_name: str, default_df: pd.DataFrame) -> pd.DataFrame:
    """
    구글 시트의 지정한 워크시트에서 데이터를 읽어 DataFrame으로 반환.
    시트가 비어있거나 읽기 실패 시 default_df 구조의 빈 DataFrame 반환.
    """
    try:
        conn = _get_gsheets_conn()
        # ttl=0: 캐시 미사용 → 저장 직후에도 최신 데이터 조회 가능
        df = conn.read(worksheet=worksheet_name, ttl=0)
        if df is None or df.empty:
            return default_df.copy()
        # 컬럼명이 기대와 다르면(예: 빈 시트의 헤더) 기본 구조 반환
        if not all(c in df.columns for c in default_df.columns):
            return default_df.copy()
        return df
    except Exception:
        return default_df.copy()


def _save_goals_safe(goals_df: pd.DataFrame) -> None:
    """goals 시트 저장 시 Goal*_Done 불리언을 문자열로 바꿔 구글 시트 API 오류를 방지."""
    out = goals_df.copy()
    for c in ["Goal1_Done", "Goal2_Done", "Goal3_Done"]:
        if c in out.columns:
            out[c] = out[c].apply(lambda x: "TRUE" if _to_bool(x) else "FALSE")
    save_data(out, WORKSHEET_GOALS)


def _save_day_type_safe(day_type_df: pd.DataFrame) -> None:
    """day_type 시트 저장 시 Use30Min 불리언을 문자열로 바꿔 구글 시트 API 오류를 방지."""
    out = day_type_df.copy()
    if "Use30Min" in out.columns:
        out["Use30Min"] = out["Use30Min"].apply(lambda x: "TRUE" if _to_bool(x) else "FALSE")
    save_data(out, WORKSHEET_DAY_TYPE)


def save_data(df: pd.DataFrame, worksheet_name: str) -> None:
    """
    DataFrame 전체를 해당 워크시트에 덮어쓰기(Update).
    API 내부적으로 시트 clear 후 set_with_dataframe으로 전체 행 기록.
    """
    try:
        conn = _get_gsheets_conn()
        conn.update(worksheet=worksheet_name, data=df)
    except GSpreadAPIError:
        st.error(
            "**구글 시트 접근 실패** — 스프레드시트를 Service Account 이메일(Secrets의 `client_email`)과 "
            "**편집자** 권한으로 공유했는지 확인하세요. Streamlit Cloud: Manage app → Settings → Secrets 에서 `client_email` 값을 복사해, 구글 시트 **공유**에 해당 이메일을 추가하세요."
        )
        raise


# 페이지 설정
st.set_page_config(page_title="Time Tracker", layout="wide")
st.title("⏱️ 데일리 시간 관리 앱")

# --- [날짜 선택기 (과거/오늘 조회용)] [F-01] ---
selected_date = st.date_input("📅 조회 및 기록할 날짜를 선택하세요", date.today())
selected_date_str = str(selected_date)

# --- [데이터 초기화 및 로드] — 구글 시트 워크시트에서 Select ---
# 1. 목표 데이터 (워크시트: goals) — 기존 시트(4컬럼)와 호환, Goal*_Done은 없으면 추가
default_goals_minimal = pd.DataFrame({"Date": [], "Goal1": [], "Goal2": [], "Goal3": []})
goals_df = load_data(WORKSHEET_GOALS, default_goals_minimal)
goals_df = _normalize_date_col(goals_df)
goals_df[["Goal1", "Goal2", "Goal3"]] = goals_df[["Goal1", "Goal2", "Goal3"]].fillna("")
for c in ["Goal1_Done", "Goal2_Done", "Goal3_Done"]:
    if c not in goals_df.columns:
        goals_df[c] = False
    goals_df[c] = goals_df[c].apply(_to_bool)

if selected_date_str not in goals_df["Date"].values:
    new_row = pd.DataFrame([{
        "Date": selected_date_str, "Goal1": "", "Goal2": "", "Goal3": "",
        "Goal1_Done": False, "Goal2_Done": False, "Goal3_Done": False
    }])
    goals_df = pd.concat([goals_df, new_row], ignore_index=True)
    _save_goals_safe(goals_df)

# 해당 날짜 행이 없으면 빈 행 추가 (날짜 형식 불일치 등으로 비교 실패 시 대비)
goals_for_date = goals_df[goals_df["Date"] == selected_date_str]
if goals_for_date.empty:
    new_row = pd.DataFrame([{
        "Date": selected_date_str, "Goal1": "", "Goal2": "", "Goal3": "",
        "Goal1_Done": False, "Goal2_Done": False, "Goal3_Done": False
    }])
    goals_df = pd.concat([goals_df, new_row], ignore_index=True)
current_goals = goals_df[goals_df["Date"] == selected_date_str].iloc[0]

# 2. 요일 유형 (출근일/휴일) 및 30분 단위 설정 — 기존 시트(2컬럼)와 호환, Use30Min 없으면 추가
default_day_type_minimal = pd.DataFrame({"Date": [], "DayType": []})
day_type_df = load_data(WORKSHEET_DAY_TYPE, default_day_type_minimal)
day_type_df = _normalize_date_col(day_type_df)
if "Use30Min" not in day_type_df.columns:
    day_type_df["Use30Min"] = False
day_type_df["Use30Min"] = day_type_df["Use30Min"].apply(_to_bool)

if day_type_df.empty or selected_date_str not in day_type_df["Date"].values:
    day_type = "holiday"
    use_30min_saved = False
else:
    row = day_type_df[day_type_df["Date"] == selected_date_str].iloc[0]
    day_type = row["DayType"]
    if day_type not in ("business", "holiday"):
        day_type = "holiday"
    use_30min_saved = _to_bool(row.get("Use30Min", False))

# 3. 시간표 데이터 (워크시트: timetable) — 슬롯은 해당 날짜의 Use30Min(use_30min_saved) 기준
default_timetable = pd.DataFrame({"Date": [], "시간": [], "활동 내용": [], "카테고리": []})
timetable_df = load_data(WORKSHEET_TIMETABLE, default_timetable)
timetable_df = _normalize_date_col(timetable_df)
slots = get_slots(day_type, use_30min_saved)

existing = timetable_df[timetable_df["Date"] == selected_date_str]
rows = []
for t in slots:
    match = existing[existing["시간"] == t]
    if not match.empty:
        rows.append({"Date": selected_date_str, "시간": t, "활동 내용": _str_or_blank(match.iloc[0]["활동 내용"]), "카테고리": _str_or_blank(match.iloc[0]["카테고리"])})
    else:
        rows.append({"Date": selected_date_str, "시간": t, "활동 내용": "", "카테고리": ""})
current_timetable = pd.DataFrame(rows)

# 4. 누적 타이머 데이터 (워크시트: cumulative) — 측정 시간대 기록용 '기록내역' 컬럼 포함
default_cumulative = pd.DataFrame({"Date": [], "활동명": [], "누적분": [], "기록내역": []})
cumulative_df = load_data(WORKSHEET_CUMULATIVE, default_cumulative)
cumulative_df = _normalize_date_col(cumulative_df)
if "기록내역" not in cumulative_df.columns:
    cumulative_df["기록내역"] = ""
cumulative_df["기록내역"] = cumulative_df["기록내역"].fillna("")
current_cumulative = cumulative_df[cumulative_df["Date"] == selected_date_str]

# --- [세션 상태 (타이머용)] ---
if 'timer_running' not in st.session_state:
    st.session_state.timer_running = False
if 'start_time' not in st.session_state:
    st.session_state.start_time = None
if 'pending_elapsed_minutes' not in st.session_state:
    st.session_state.pending_elapsed_minutes = None
if 'pending_time_range_str' not in st.session_state:
    st.session_state.pending_time_range_str = ""  # 종료 시 활동명 미입력 시, 나중에 기록내역에 쓸 "HH:MM-HH:MM"

# ==========================================
# UI 구현 (F-01 ~ F-04 동작 유지)
# ==========================================

# --- [1. 오늘 목표 TOP 3] [F-02] ---
st.header(f"🎯 {selected_date_str} 목표 TOP 3")
col1, col2, col3 = st.columns(3)

with col1:
    g1 = st.text_input("목표 1", value=_str_or_blank(current_goals["Goal1"]), key=f"goal1_{selected_date_str}")
    d1 = st.checkbox("완료", value=_to_bool(current_goals.get("Goal1_Done", False)), key=f"goal1_done_{selected_date_str}")
with col2:
    g2 = st.text_input("목표 2", value=_str_or_blank(current_goals["Goal2"]), key=f"goal2_{selected_date_str}")
    d2 = st.checkbox("완료", value=_to_bool(current_goals.get("Goal2_Done", False)), key=f"goal2_done_{selected_date_str}")
with col3:
    g3 = st.text_input("목표 3", value=_str_or_blank(current_goals["Goal3"]), key=f"goal3_{selected_date_str}")
    d3 = st.checkbox("완료", value=_to_bool(current_goals.get("Goal3_Done", False)), key=f"goal3_done_{selected_date_str}")

# 목표 저장 버튼 — 텍스트 + 수행 완료 체크 상태를 구글 시트에 반영 후 화면 갱신
if st.button("💾 목표 저장", type="primary", key=f"goals_save_{selected_date_str}"):
    goals_df.loc[goals_df["Date"] == selected_date_str, ["Goal1", "Goal2", "Goal3"]] = [g1 or "", g2 or "", g3 or ""]
    goals_df.loc[goals_df["Date"] == selected_date_str, ["Goal1_Done", "Goal2_Done", "Goal3_Done"]] = [d1, d2, d3]
    _save_goals_safe(goals_df)
    st.success("목표가 저장되었습니다.")
    st.rerun()

st.divider()

left_col, right_col = st.columns([1, 1.5])

# --- [3. 시간 타이머 & 누적 체크] [F-03] ---
with left_col:
    st.header("⏳ 활동 타이머")

    activity_name = st.text_input("현재 진행할 활동을 입력하세요 (예: 공부, 운동)", placeholder="비워두고 시작해도 됩니다. 종료 시 입력 가능")

    # 종료 후 활동명 미입력 시 입력받기
    if st.session_state.pending_elapsed_minutes is not None:
        with st.form("pending_activity_form"):
            late_name = st.text_input("측정한 활동 이름을 입력하세요 (예: 공부, 운동)")
            if st.form_submit_button("저장"):
                if late_name and late_name.strip():
                    pending_mins = st.session_state.pending_elapsed_minutes
                    time_range_str = st.session_state.get("pending_time_range_str", "")
                    cum_df = load_data(WORKSHEET_CUMULATIVE, default_cumulative)
                    if "기록내역" not in cum_df.columns:
                        cum_df["기록내역"] = ""
                    cum_df["기록내역"] = cum_df["기록내역"].fillna("")
                    curr = cum_df[cum_df["Date"] == selected_date_str]
                    if late_name.strip() in curr["활동명"].values:
                        mask = (cum_df["Date"] == selected_date_str) & (cum_df["활동명"] == late_name.strip())
                        cum_df.loc[mask, "누적분"] += pending_mins
                        if time_range_str:
                            prev = cum_df.loc[mask, "기록내역"].iloc[0]
                            cum_df.loc[mask, "기록내역"] = (prev + ", " + time_range_str) if prev else time_range_str
                    else:
                        new_row = pd.DataFrame([{"Date": selected_date_str, "활동명": late_name.strip(), "누적분": pending_mins, "기록내역": time_range_str}])
                        cum_df = pd.concat([cum_df, new_row], ignore_index=True)
                    save_data(cum_df, WORKSHEET_CUMULATIVE)
                    st.session_state.pending_elapsed_minutes = None
                    st.session_state.pending_time_range_str = ""
                    st.success(f"[{late_name.strip()}] {pending_mins}분 저장 완료!")
                    st.rerun()
                else:
                    st.warning("활동명을 입력해주세요.")

    timer_col1, timer_col2 = st.columns(2)

    with timer_col1:
        if st.button("▶️ 시작", use_container_width=True, disabled=st.session_state.timer_running):
            st.session_state.timer_running = True
            st.session_state.start_time = time.time()
            st.rerun()

    with timer_col2:
        if st.button("⏹️ 종료 및 저장", use_container_width=True, disabled=not st.session_state.timer_running):
            st.session_state.timer_running = False
            elapsed_seconds = time.time() - st.session_state.start_time
            elapsed_minutes = max(1, int(elapsed_seconds // 60))
            # 측정 시간대 문자열 (예: "22:55-22:56")
            start_dt = datetime.fromtimestamp(st.session_state.start_time)
            end_dt = datetime.now()
            time_range_str = start_dt.strftime("%H:%M") + "-" + end_dt.strftime("%H:%M")

            if activity_name and activity_name.strip():
                cum_df = load_data(WORKSHEET_CUMULATIVE, default_cumulative)
                if "기록내역" not in cum_df.columns:
                    cum_df["기록내역"] = ""
                cum_df["기록내역"] = cum_df["기록내역"].fillna("")
                curr = cum_df[cum_df["Date"] == selected_date_str]
                if activity_name.strip() in curr["활동명"].values:
                    mask = (cum_df["Date"] == selected_date_str) & (cum_df["활동명"] == activity_name.strip())
                    cum_df.loc[mask, "누적분"] += elapsed_minutes
                    prev = cum_df.loc[mask, "기록내역"].iloc[0]
                    cum_df.loc[mask, "기록내역"] = (prev + ", " + time_range_str) if prev else time_range_str
                else:
                    new_row = pd.DataFrame([{"Date": selected_date_str, "활동명": activity_name.strip(), "누적분": elapsed_minutes, "기록내역": time_range_str}])
                    cum_df = pd.concat([cum_df, new_row], ignore_index=True)
                save_data(cum_df, WORKSHEET_CUMULATIVE)
                st.success(f"[{activity_name.strip()}] {elapsed_minutes}분 저장 완료!")
                st.rerun()
            else:
                st.session_state.pending_elapsed_minutes = elapsed_minutes
                st.session_state.pending_time_range_str = time_range_str
                st.rerun()

    if st.session_state.timer_running:
        st.info("타이머가 작동 중입니다... (종료 버튼을 눌러야 저장됩니다)")

    st.subheader("📊 누적 활동 시간")
    display_cumulative = cumulative_df[cumulative_df["Date"] == selected_date_str]
    if not display_cumulative.empty:
        for _, row in display_cumulative.iterrows():
            st.metric(label=row["활동명"], value=f"{int(row['누적분'])} 분")
            # 측정 시간대 상세 (예: "22:55-22:56, 22:57-22:59 (총 3분)")
            record = _str_or_blank(row.get("기록내역", ""))
            if record:
                st.caption(f"{record} (총 {int(row['누적분'])}분)")
    else:
        st.write("해당 날짜에 기록된 타이머 활동이 없습니다.")

# --- [2. 시간표 (06:00 ~ 23:00)] [F-04] ---
with right_col:
    st.header("📅 시간표 (Time Table)")

    # [F-04] 회사 출근일 / 휴일 선택
    new_day_type = st.selectbox(
        "오늘의 유형",
        options=["holiday", "business"],
        format_func=lambda x: "휴일 (06:00~23:00)" if x == "holiday" else "회사 출근일 (06-09, 12-13, 18-23)",
        index=0 if day_type == "holiday" else 1,
        key=f"daytype_{selected_date_str}"
    )
    if new_day_type != day_type:
        if day_type_df.empty or selected_date_str not in day_type_df["Date"].values:
            day_type_df = pd.concat([day_type_df, pd.DataFrame([{"Date": selected_date_str, "DayType": new_day_type, "Use30Min": use_30min_saved}])], ignore_index=True)
        else:
            day_type_df.loc[day_type_df["Date"] == selected_date_str, "DayType"] = new_day_type
        _save_day_type_safe(day_type_df)
        st.rerun()

    # [F-04] 30분 단위 확장 — 상태는 day_type 시트의 Use30Min으로 저장·로드
    use_30min = st.checkbox("30분 단위로 입력하기", value=use_30min_saved, key=f"30min_{selected_date_str}")
    if use_30min != use_30min_saved:
        if day_type_df.empty or selected_date_str not in day_type_df["Date"].values:
            day_type_df = pd.concat([day_type_df, pd.DataFrame([{"Date": selected_date_str, "DayType": day_type, "Use30Min": use_30min}])], ignore_index=True)
        else:
            day_type_df.loc[day_type_df["Date"] == selected_date_str, "Use30Min"] = use_30min
        _save_day_type_safe(day_type_df)
        st.rerun()

    st.caption("수정 후 아래 [💾 시간표 저장] 버튼을 누르면 구글 시트에 저장됩니다.")
    display_df = current_timetable[["시간", "활동 내용", "카테고리"]].copy()
    # None/NaN을 빈 문자열로 치환해 에디터에 'None'이 보이지 않도록 함
    display_df["활동 내용"] = display_df["활동 내용"].apply(lambda x: _str_or_blank(x))
    display_df["카테고리"] = display_df["카테고리"].apply(lambda x: _str_or_blank(x))

    edited_df = st.data_editor(
        display_df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"editor_{selected_date_str}_{day_type}_{use_30min_saved}"
    )

    # 시간표는 저장 버튼 클릭 시에만 구글 시트에 일괄 저장 (잦은 API 호출 방지)
    if st.button("💾 시간표 저장", type="primary", key=f"timetable_save_{selected_date_str}"):
        timetable_df = timetable_df[timetable_df["Date"] != selected_date_str]
        for i, row in edited_df.iterrows():
            timetable_df = pd.concat([timetable_df, pd.DataFrame([{"Date": selected_date_str, "시간": row["시간"], "활동 내용": row["활동 내용"], "카테고리": row["카테고리"]}])], ignore_index=True)
        save_data(timetable_df, WORKSHEET_TIMETABLE)
        st.success("시간표가 저장되었습니다.")
        st.rerun()
