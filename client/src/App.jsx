import { useState } from "react";
const API_BASE = import.meta.env.VITE_API_BASE;

export default function App() {
  const [code, setCode] = useState("CS1331");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function search() {
    setLoading(true);
    setError("");
    setResults([]);
    try {
      const res = await fetch(`${API_BASE}/api/equivalents?gt=${encodeURIComponent(code)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setResults(data.items || []);
    } catch (e) {
      setError("Could not load results. Make sure the server at port 5175 is running.");
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter") search();
  }

  return (
    <div className="page">
      {/* HERO */}
      <section className="hero">
        <div className="hero-inner">
          <h1 className="title">TransferMap GT</h1>
          <p className="subtitle">
            View Georgia schools that have courses equivalent to a GT course.
            Type a code like CS1331 or CS 1331 and press Search.
          </p>

          <div className="search">
            <input
              className="input"
              value={code}
              onChange={e => setCode(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Search for a GT course like CS1331"
              aria-label="GT course code"
            />
            <button className="btn" onClick={search} disabled={loading}>
              {loading ? "Searching..." : "Search"}
            </button>
          </div>

          {error && (
            <div className="note" style={{ marginTop: 14, borderStyle: "solid", borderColor: "crimson", color: "crimson" }}>
              {error}
            </div>
          )}
        </div>
      </section>

      {/* RESULTS */}
      <section className="section">
        {results.length === 0 && !loading && !error && (
          <div className="note">No results yet. Try CS1331.</div>
        )}

        {results.map((r, i) => (
          <article className="card" key={i}>
            <h3>{r.schoolName}</h3>
            <div className="line">
              {r.gtCourseCode} — {r.gtCourseName} → {r.externalCourseCode} — {r.externalCourseName}
            </div>
            <div className="grid">
              <div><strong>GT credit hours:</strong> {r.gtCreditHours}</div>
              <div><strong>External credit hours:</strong> {r.externalCreditHours}</div>
              <div><strong>Semester:</strong> {r.semester}</div>
            </div>
          </article>
        ))}
      </section>

      <footer className="footer">
        © {new Date().getFullYear()} TransferMap GT
      </footer>
    </div>
  );
}
