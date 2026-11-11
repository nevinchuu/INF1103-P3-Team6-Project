# Data Flow and Testing

> Draft for the INF1103 Part 2 report. This section describes the current
> implementation in the repository; update it if the architecture or test
> coverage changes.

## Data flow

The application has console and web front ends. Both use the same search
workflow, which connects the resume and job portal inputs to AI analysis,
business rules, and saved results. The existing
[data-flow diagram](../diagrams/dataflow.mmd) gives a visual overview.

### Job search

1. The user selects or provides a resume and search preferences in the
   `ConsoleApp` or `WebApp`.
2. `IOManager` reads the resume and checks or requests any missing search
   details. `AIManager` uses the configured AI provider to extract a candidate
   profile, including search keywords and skills.
3. `SearchService` coordinates the search. It asks `JobPortal` to fetch
   MyCareersFuture listings and applies the filters that can be used during
   retrieval.
4. `AIManager` analyses the retrieved listings to extract their requirements.
   `LogicManager` combines those requirements with the candidate profile,
   applies the remaining filters, and ranks the matches.
5. The front end displays the ranked results and match details. `DataManager`
   saves the best results through the configured `Storage` implementation.

`JsonStorage` is the default backend; `SqliteStorage` is also available.
Saved jobs persist between runs, while search results are held in memory for
the current session.

### Other user flows

- **Resume tailoring:** the user selects a job and resume; AI-generated edits
  are checked by `TailoringGuard`, and `DocxEditor` reads or writes Word
  documents for the web workflow.
- **What-if and roadmap:** `CareerSimulator` applies hypothetical skills to
  the current job results and re-ranks them using `LogicManager`. Its roadmap
  planners choose skills without making AI calls.
- **Saved jobs:** the front ends use `DataManager` to search, update, and
  remove jobs through the selected storage backend.

## Error handling and data integrity

External and persisted data is validated before it is used. `AIManager`
parses and validates provider responses; AI providers handle transient
request failures with retries. `JobPortal` handles failed or unusable portal
responses. Storage implementations handle missing or corrupt saved-job data,
and report storage errors to the application. User-provided filters and
what-if skills are checked before the workflow continues.

The intended behavior is to show a useful error or request corrected input
rather than crash or save invalid results. The existing
[exception-flow diagram](../diagrams/exception_flow.mmd) documents these
paths.

## Testing

Run the complete test suite from the repository root:

```powershell
python -m pytest
```

Tests are designed to run without a live AI provider, a real job portal, or
personal data files. Test doubles, stubbed HTTP responses, temporary files,
and isolated test data make results repeatable and avoid external API calls.

Coverage includes:

- AI response parsing, schema validation, and provider failure cases.
- Portal response handling, filtering, matching, and ranking rules.
- Saving, loading, and updating jobs with both JSON and SQLite storage.
- Resume handling, tailoring safeguards, and Word document editing.
- Roadmap and what-if behavior, including invalid inputs.
- Console and web application behavior, task progress, and architecture
  diagrams.

The GitHub Actions workflow runs tests and additional checks on pushes and
pull requests. The repository README describes the CI jobs, including the
Docker build and container test run.

## Limitations

Live job listings can change between searches. Search results are not retained
after the application restarts, although saved jobs are persistent. The
application is intended for one user on their own computer.