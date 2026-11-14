---

## About the Project

**Smart Question Paper Generator** is an AI-powered web application designed to simplify and automate the process of creating question papers. It helps teachers generate structured question papers from uploaded study materials using Artificial Intelligence.

The application uses Google Gemini AI to generate relevant questions based on the selected subject, question type, difficulty level, and required number of questions.

### Key Features

- **PDF Upload:** Upload study materials in PDF format.
- **AI-Based Question Generation:** Generate questions using Google Gemini AI.
- **Multiple Question Types:** Supports MCQs, short-answer questions, and long-answer questions.
- **Difficulty Levels:** Choose Easy, Medium, Hard, or Mixed difficulty.
- **Question Bank:** Maintain and manage generated questions.
- **Role-Based Access:** Separate access for Teachers, HOD, and Coordinator.
- **Question Paper Management:** Create, schedule, and manage question papers.
- **PDF Export:** Generate and download question papers in PDF format.
- **Dashboard:** Manage teachers, question papers, and question banks.

### Technology Stack

| Category | Technologies |
|---|---|
| Frontend | HTML, CSS, JavaScript, Bootstrap |
| Backend | Python, Flask |
| Database | SQLite |
| AI Integration | Google Gemini API (Gemini 2.5 Flash) |
| PDF Processing | PyPDF2 |
| PDF Generation | ReportLab |

### Application Workflow

1. User logs into the application.
2. Selects the subject.
3. Uploads the relevant PDF study material.
4. Selects the question type.
5. Specifies the number of questions.
6. Chooses the difficulty level.
7. AI generates questions based on the uploaded content.
8. Generated questions can be managed and used to prepare question papers.
9. The Coordinator manages and downloads the finalized question papers.

### AI Integration

The application integrates Google Gemini AI to analyze uploaded study materials and generate questions according to the selected requirements. This reduces manual effort and makes question paper preparation more efficient.

### Security

- Environment variables are used to manage sensitive configuration values.
- Uploaded files are processed through the application.
- Role-based access helps manage application functionality.

### Limitations

- An active internet connection is required for Gemini AI question generation.
- Generated questions may require review and verification by teachers.
- Question paper generation follows predefined formats and configurations.

### Future Enhancements

- Support for additional document formats.
- Advanced question quality evaluation.
- More customizable question paper templates.
- Improved analytics and reporting.
- Additional AI-powered academic features.

---

**Developed as an academic project using Python, Flask, SQLite, and Google Gemini AI.**
