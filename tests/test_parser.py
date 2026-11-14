import pytest
from core.parser import parse_counters


def test_parse_counters_valid_file(tmp_path):
    # Создаем временный файл с тестовыми данными
    test_file = tmp_path / "CountData0.csv"
    test_file.write_text(
        '"VarName";"TimeString";"VarValue";"Validity";"Time_ms"\n"#V_L";"17.08.2026 9:09:50";28;1;46251381829,8958\n')

    records = parse_counters(str(test_file))

    assert len(records) == 1
    assert records[0].var_name == "#V_L"
    assert records[0].var_value == 28.0