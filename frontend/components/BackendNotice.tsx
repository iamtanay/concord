export default function BackendNotice() {
  return (
    <section className="notice">
      <h2>The backend is not running</h2>
      <p>Start the backend on :8000, then reload this page.</p>
      <pre>
        {`cd backend
.venv\\Scripts\\activate
uvicorn app:app --port 8000`}
      </pre>
    </section>
  );
}
