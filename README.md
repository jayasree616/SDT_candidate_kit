# Employee Attendance & Analytics API

A REST API built with **Python, FastAPI, and MongoDB** to manage employee attendance, punch-in/punch-out records, attendance corrections, and analytics.

## Features

- Employee creation and listing with pagination.
- Employee attendance punch-in and punch-out.
- Attendance record listing with filters and pagination.
- Attendance regularization with audit history.
- Employee monthly attendance analytics.
- Department-wise attendance summaries.
- Late-arrival leaderboard.
- Department attendance trends over a date range.
- MongoDB aggregation pipelines for analytics.
- Query execution statistics through the admin explain endpoint.
- MongoDB indexes for efficient data retrieval.

## Technology Stack

- Python 3.13
- FastAPI
- PyMongo
- MongoDB
- Uvicorn
- Pydantic
- python-dotenv

## Project Structure

```text
candidate_kit/
├── app/
│   ├── __init__.py
│   └── main.py
├── sample_data/
│   ├── attendance_logs.json
│   └── employees.json
├── DATA_MODEL.md
├── DECISIONS.md
├── README.md
├── REVIEW.md
├── openapi.yaml
├── requirements.txt
├── sample_seed.py
└── .gitignore
```

## Prerequisites

- Python 3.11 or later.
- A MongoDB database, such as MongoDB Atlas.
- Git (optional, for version control).

## Setup and Installation

### 1. Clone the repository

```bash
git clone https://github.com/jayasree616/SDT_candidate_kit.git
cd SDT_candidate_kit
```

### 2. Create a virtual environment

**Windows PowerShell:**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, use Command Prompt and run:

```cmd
venv\Scripts\activate.bat
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a local `.env` file in the project root. Do not commit this file to GitHub.

```dotenv
MONGO_URI=your_mongodb_connection_string
MONGO_DB=your_database_name
```

Replace the example values with your actual MongoDB connection string and database name.

Ensure your MongoDB network access and database credentials are configured correctly.

## Running the Application

From the project root, run:

```bash
uvicorn app.main:app --reload --port 8000
```

The API will be available at:

- **Base URL:** http://127.0.0.1:8000
- **Interactive Swagger documentation:** http://127.0.0.1:8000/docs
- **OpenAPI schema:** http://127.0.0.1:8000/openapi.json

Use Swagger UI to explore the available endpoints and test requests.

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/employees` | Create an employee |
| GET | `/employees` | List employees |
| POST | `/attendance/punch-in` | Record punch-in |
| POST | `/attendance/punch-out` | Record punch-out |
| GET | `/attendance` | List and filter attendance records |
| PATCH | `/attendance/{emp_code}/{date}` | Correct an attendance record |
| GET | `/analytics/employees/{emp_code}/monthly` | Employee monthly analytics |
| GET | `/analytics/departments/summary` | Department attendance summary |
| GET | `/analytics/leaderboard/late` | Late-arrival leaderboard |
| GET | `/analytics/departments/{department}/trend` | Department attendance trend |
| GET | `/admin/explain/{endpoint}` | Query execution statistics |

Refer to `/docs` for request schemas, query parameters, and response formats.

## Data and Calculation Conventions

- Employee codes identify employees in API operations.
- Attendance records are associated with an employee and attendance date.
- API timestamps use epoch milliseconds.
- Attendance dates and shift calculations follow the assignment's India Standard Time (IST) requirements.
- Attendance analytics include working-day calculations, late arrivals, overtime, work hours, and half-day status.
- Attendance corrections maintain an audit history.
- Pagination uses a page number and page size.

## Database and Indexes

The application uses MongoDB collections for employee and attendance data. Indexes are created during application startup to support employee lookup, attendance uniqueness, date filtering, and analytics queries.

Query execution statistics are available through the admin explain endpoint for supported query types.

## Documentation

- `DATA_MODEL.md` — data model documentation.
- `DECISIONS.md` — design decisions.
- `REVIEW.md` — starter-code review and identified issues.
- `openapi.yaml` — OpenAPI specification.

## Security

- Keep `.env` and database credentials out of version control.
- Use environment variables for database configuration.
- Do not publish passwords, connection strings, or other secrets.
- Restrict database access to trusted users and networks.

## License

This project was developed as part of a software engineering take-home assignment.
