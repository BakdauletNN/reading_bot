from datetime import date

from bot import add_friend, calculate_question_count, create_progress_report, update_progress


def test_update_progress_increases_streak_when_reading_on_next_day(monkeypatch):
    import bot

    profile = {"pages_read": 0, "last_read_date": None, "streak": 0, "best_streak": 0}
    update_progress(profile, 20)
    assert profile["pages_read"] == 20
    assert profile["streak"] == 1
    assert profile["best_streak"] == 1


def test_add_friend_adds_username_once():
    profile = {"friends": []}
    result = add_friend(profile, "@ali")
    assert result == "@ali добавлен в друзья."
    assert profile["friends"] == ["ali"]


def test_calculate_question_count_matches_requested_scale():
    assert calculate_question_count(1) == 2
    assert calculate_question_count(20) == 5
    assert calculate_question_count(50) == 10


def test_create_progress_report_contains_streak_and_pages():
    profile = {
        "pages_read": 120,
        "streak": 5,
        "best_streak": 7,
        "daily_pages_goal": 20,
        "last_read_date": date.today().isoformat(),
        "book_title": "Test Book",
        "read_history": [{"date": date.today().isoformat(), "pages": 20}],
    }
    report = create_progress_report(profile)
    assert "🔥 Серия" in report
    assert "📄 Прочитано страниц" in report
    assert "📈" in report
