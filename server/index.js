// server/index.js

import express from "express";
import cors from "cors";
import Database from "better-sqlite3";
import fs from "node:fs";
import path from "node:path";

// Ensure the data folder exists and open the DB file
const dbPath = path.resolve("../data/transfermap.db");
fs.mkdirSync(path.dirname(dbPath), { recursive: true });
const db = new Database(dbPath);

// Turn on foreign key enforcement in SQLite
db.exec(`PRAGMA foreign_keys = ON;`);

// Create tables if they do not exist
db.exec(`
CREATE TABLE IF NOT EXISTS School (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS GTCourse (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  code        TEXT NOT NULL UNIQUE,   -- example: "CS 1331"
  title       TEXT NOT NULL,
  creditHours REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ExternalCourse (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  schoolId    INTEGER NOT NULL,
  code        TEXT NOT NULL,          -- example: "CSC 2510"
  title       TEXT NOT NULL,
  creditHours REAL NOT NULL,
  FOREIGN KEY (schoolId) REFERENCES School(id) ON DELETE CASCADE,
  UNIQUE (schoolId, code)
);

CREATE TABLE IF NOT EXISTS Equivalency (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  gtCourseId         INTEGER NOT NULL,
  schoolId           INTEGER NOT NULL,
  externalCourseCode TEXT    NOT NULL,
  semester           TEXT    NOT NULL,  -- example: "Fall 2025"
  FOREIGN KEY (gtCourseId) REFERENCES GTCourse(id) ON DELETE CASCADE,
  FOREIGN KEY (schoolId)   REFERENCES School(id)   ON DELETE CASCADE,
  UNIQUE (gtCourseId, schoolId, externalCourseCode)
);

CREATE INDEX IF NOT EXISTS ix_gtcourse_code ON GTCourse(code);
CREATE INDEX IF NOT EXISTS ix_equiv_gt ON Equivalency(gtCourseId);
CREATE INDEX IF NOT EXISTS ix_equiv_school ON Equivalency(schoolId);
`);

// Seed a few rows only if empty
const seeded = db.prepare("SELECT COUNT(*) AS c FROM GTCourse").get().c > 0;
if (!seeded) {
  const insSchool = db.prepare("INSERT INTO School(name) VALUES (?)");
  const insGT = db.prepare("INSERT INTO GTCourse(code, title, creditHours) VALUES (?, ?, ?)");
  const insExt = db.prepare("INSERT INTO ExternalCourse(schoolId, code, title, creditHours) VALUES (?, ?, ?, ?)");
  const insEq = db.prepare(
    "INSERT INTO Equivalency(gtCourseId, schoolId, externalCourseCode, semester) VALUES (?, ?, ?, ?)"
  );

  const gsuId = insSchool.run("Georgia State University").lastInsertRowid;
  const ksuId = insSchool.run("Kennesaw State University").lastInsertRowid;

  const cs1331Id = insGT.run("CS 1331", "Introduction to Object Oriented Programming", 3).lastInsertRowid;
  const cs1332Id = insGT.run("CS 1332", "Data Structures and Algorithms", 3).lastInsertRowid;

  // External courses
  insExt.run(gsuId, "CSC 2510", "Object Oriented Programming", 3);
  insExt.run(ksuId, "CS 2302", "Object Oriented Programming", 3);
  insExt.run(gsuId, "CSC 2720", "Data Structures", 3);

  // Equivalencies - using your Semester field
  insEq.run(cs1331Id, gsuId, "CSC 2510", "Fall 2025");
  insEq.run(cs1331Id, ksuId, "CS 2302", "Fall 2025");
  insEq.run(cs1332Id, gsuId, "CSC 2720", "Fall 2025");
}

// Helper: normalize CS1331 or cs 1331 to "CS 1331"
function normalizeGT(input) {
  const raw = String(input || "").trim().toUpperCase();
  if (!raw) return "";
  return raw.replace(/\s*/g, "").replace(/^([A-Z]+)(\d+)/, "$1 $2");
}

const app = express();
app.use(cors());
app.use(express.json());

// GET /api/equivalents?gt=CS1331 or CS 1331
app.get("/api/equivalents", (req, res) => {
  const gtParam = normalizeGT(req.query.gt);
  if (!gtParam) {
    return res.status(400).json({ error: "gt is required. Example: /api/equivalents?gt=CS1331" });
  }

  // Find the GT course row by code ignoring spaces
  const gt = db
    .prepare('SELECT id, code, title, creditHours FROM GTCourse WHERE REPLACE(code, \' \', \'\') = REPLACE(@c, \' \', \'\')')
    .get({ c: gtParam });

  if (!gt) return res.json({ items: [], total: 0 });

  // Join Equivalency with School and ExternalCourse
  const rows = db
    .prepare(
      `
      SELECT
        s.name AS schoolName,
        gc.code AS gtCourseCode,
        gc.title AS gtCourseName,
        gc.creditHours AS gtCreditHours,
        ec.code AS externalCourseCode,
        ec.title AS externalCourseName,
        ec.creditHours AS externalCreditHours,
        e.semester AS semester
      FROM Equivalency e
      JOIN School s ON s.id = e.schoolId
      JOIN GTCourse gc ON gc.id = e.gtCourseId
      LEFT JOIN ExternalCourse ec
        ON ec.schoolId = e.schoolId
       AND ec.code = e.externalCourseCode
      WHERE e.gtCourseId = @gtId
      ORDER BY s.name, ec.code
    `
    )
    .all({ gtId: gt.id });

  res.json({ items: rows, total: rows.length });
});

const PORT = 5175;
app.listen(PORT, () => {
  console.log(`API running on http://localhost:${PORT}`);
});
