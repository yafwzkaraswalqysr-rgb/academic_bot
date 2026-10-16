# Academic Bot — Student / Owner Edition

This release keeps the existing project and database schema/data, adding only additive tables/columns and compatibility migrations. No database file is shipped inside this ZIP.

## Local / Termux
```bash
pkg update -y
pkg install python python-lxml -y
python -m venv ~/academic_venv
source ~/academic_venv/bin/activate
pip install -r requirements.txt
```

Create `.env` from `.env.example`, set the required variables, then:
```bash
python bot.py
```

## Existing database safety
At startup the bot runs additive migrations only. Existing `users`, searches, school content, placements, and other records are not dropped/recreated. Legacy `school_contents` rows are automatically mirrored into `school_placements` when no placement exists.

## School search and library
Owner-added books, handouts, PDFs, DOCX, PPTX, TXT, media, links, summaries, teachers, explanations, ministerial content and other supported school content are searchable locally. The search reads the database, so it still works after a restart.

A single `SchoolContent` can have many `SchoolPlacement` rows. Removing one placement does not remove the content record. Deleting the whole content removes its placements first; Telegram-hosted files are not remotely deleted by the bot.

## University
University resources and graduation projects have their own database tables and management flow. University search can combine owner-managed resources with configured external providers. School and university search paths do not mix results.

University resource/project requests support caller-selected counts (10/20/50 or a custom value), provider pagination, and real-availability reporting. Custom requests are silently bounded by an internal safety budget that is not exposed in the user experience; providers still have their own API/page constraints.

## Academic source purposes

Owner-managed Telegram channels and websites can be assigned to `university_search`, `graduation`, or `both`. A university search only consumes sources assigned to university search/both; a graduation search only consumes graduation/both. Legacy `general` or empty source-purpose values are migrated to `both` without deleting source records.

## AI study session
The current material is stored only in `study_sessions` as the authoritative state, not inferred from analytics/FSM history. Q&A, explanations, summaries, MCQ, true/false, short answer, fill-in, matching, mixed exams and flashcards can reuse the same material. For PDFs with page markers, each service asks for the full material or an explicit page range such as `5-12`. Ending the session removes the current material, while historical analytics remain only for statistics.

Question generation is batched internally. The requested number is user-controlled; generation stops only when the model/material cannot provide new valid items. Telegram messages are chunked below the platform message ceiling. AI JSON is parsed internally and never intentionally shown raw. Common LaTeX/Markdown artifacts are normalized before display.

## Owner
`/owner` opens the protected owner/admin panel. Authorization is checked on handlers as well as UI visibility. Owner-only admin management cannot remove the owner.

The owner panel contains school/university management, books, files, Telegram sources, websites, users, statistics, AI status, and reindexing.

## Historical statistics
Usage events are stored separately from the user row and do not have a foreign-key cascade to `users`. Deleting a user therefore does not delete historical search/question/exam/explanation/summary/source/usage events.

## Railway
The included Dockerfile starts the bot with `python bot.py`. Set environment variables in Railway; do not upload `.env` or embed secrets in the source.

For persistent production data, use PostgreSQL via `DATABASE_URL` or a persistent Railway volume for SQLite. An ephemeral container filesystem is not a safe long-term database store.

No webhook is required; the bot uses Telegram polling.
