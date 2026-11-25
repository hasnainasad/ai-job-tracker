# profile/ (example)

Copy this folder to `profile/` and fill it in. `profile/` is in `.gitignore`, so your
resume never reaches GitHub.

- `master_resume.md`: your full resume as plain text (copy-paste from your PDF, or
  `pdftotext -layout resume.pdf profile/master_resume.md` on macOS with poppler).
- `templates.md`: one `## X: Name` section per resume version, with its headline and
  summary. Scoring picks the best one per job.
- `preferences.md`: optional, plain English. Lines starting with `#` are ignored.
- `templates/A.md` ... `templates/E.md`: full text of each resume version, used as the
  starting point for tailoring (phase 6).
