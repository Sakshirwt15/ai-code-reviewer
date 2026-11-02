.PHONY: install test lint run eval dashboard docker
install:
	pip install -r requirements-dev.txt
test:
	pytest -q
lint:
	ruff check .
run:
	uvicorn app.main:create_app --factory --reload --port 8000
eval:
	python -m eval.run_eval
dashboard:
	pip install -r requirements-dashboard.txt && streamlit run dashboard/app.py
docker:
	docker compose up --build
