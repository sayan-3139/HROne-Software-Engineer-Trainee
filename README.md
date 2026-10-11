# Employee Attendance & Analytics API

A REST API built with FastAPI and MongoDB for employee management, attendance tracking, manual attendance corrections, and analytics.

## Features

- Create and list employees.
- Record attendance punch-in and punch-out.
- Filter and paginate attendance records.
- Correct attendance records and maintain correction history.
- Calculate monthly employee attendance statistics.
- Generate department summaries, late-arrival rankings, and attendance trends.
- Inspect MongoDB query execution plans.

## Technology Stack

- Python
- FastAPI
- MongoDB
- PyMongo
- Pydantic

## Prerequisites

- Python 3.12 or a compatible version.
- MongoDB 6.0 or newer, including MongoDB Atlas.
- Git.

## Local Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
MONGO_URI=your_mongodb_connection_string
MONGO_DB=attendance_db
```

Replace the example connection string with your own MongoDB connection string. Never commit `.env` or database credentials to GitHub.

## Run the API

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Open the interactive API documentation at:

http://127.0.0.1:8000/docs

The health endpoint is available at:

http://127.0.0.1:8000/health

## Sample Data

The `sample_data/` directory contains sample employee and attendance data. Review `sample_seed.py` before loading sample records into your database.

## Project Files

- `app/main.py` — API implementation.
- `requirements.txt` — Python dependencies.
- `openapi.yaml` — API contract.
- `DATA_MODEL.md` — MongoDB data model.
- `REVIEW.md` — Code review findings.
- `DECISIONS.md` — Design decisions.
- `sample_data/` — Sample data.

## Submission Notes

- Keep the repository public as required by the assignment.
- Do not commit `.env`, credentials, or other secrets.
- Do not include a Dockerfile.
- Test the implementation against `openapi.yaml` and the assignment requirements before submission.
