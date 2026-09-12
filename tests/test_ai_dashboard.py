from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_test_call_is_preserved():
 s=(ROOT/"app/call_e_service.py").read_text(); assert "_build_test_payload" in s and "def start_test_call" in s and "restartai-test-" in s
def test_product_call_is_specific():
 s=(ROOT/"app/call_e_service.py").read_text(); assert "Part number:" in s and "GST/tax" in s and "earliest delivery time" in s
def test_ai_api_exists():
 s=(ROOT/"app/main.py").read_text(); assert "/api/ai/recommend/{run_id}" in s and "/api/chat" in s
def test_technician_panel_removed():
 s=(ROOT/"app/templates/index.html").read_text(); assert "Technician intelligence" not in s and "illustrative demo values" not in s
