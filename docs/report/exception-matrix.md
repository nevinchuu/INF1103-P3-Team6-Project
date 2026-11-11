# Exception handling matrix

> Draft. This version covers the input/output layer (`src/io_layer/`). The search, tailoring, console and web app
> rows are added in later commits.

Each row is one thing that can go wrong, where it is caught, and what the user sees. **Validation** means the code
checks the data before using it (an `if`); **exception** means it tries the operation and catches the error
(`try`/`except`). Both keep the program running instead of crashing.

## 1. Typed answers at the console (`IOManager`)

Every prompt asks again until the answer is usable, so a typing mistake never ends the program.

| Where | What can go wrong | Validation or exception | How it is handled | What the user sees | Test |
|---|---|---|---|---|---|
| `prompt_number` | Not a whole number (`abc`, `5.5`) | Exception: `int()` raises `ValueError` | Caught, asks again | `[!] Please enter a whole number between 1 and 10.` | `test_numbers_that_are_bad_are_rejected_and_asked_again` |
| `prompt_number` | Number outside the allowed range (`-3`, `70`) | Validation: `minimum <= value <= maximum` | Asks again | `[!] Please enter a number between 1 and 10.` | `test_numbers_that_are_bad_are_rejected_and_asked_again` |
| `prompt_number` | Money typed with `$` or commas (`$5,000`) | Validation: `$` and `,` removed before `int()` | Read as `5000` | Nothing: the answer is accepted | `test_a_number_takes_the_default_on_enter_and_reads_money_formats` |
| `prompt_text` | Blank or only spaces | Validation: empty after `strip()` | Asks again (unless blank is allowed) | `[!] This cannot be left blank.` | `test_text_cannot_be_blank_unless_allowed` |
| `prompt_yes_no` | Anything other than y / yes / n / no | Validation | Asks again | `[!] Please type y or n.` | `test_yes_no_asks_again_until_it_is_understood` |
| `prompt_choice` | A menu number that isn't listed (`0`, `9`, `x`) | Validation through `prompt_number`'s range | Asks again | `[!] Please enter a number between 1 and 3.` | `test_a_choice_can_be_picked_by_number_only_from_the_list` |
| `prompt_filters` | Maximum salary lower than minimum salary | Validation: `max_salary >= min_salary` | Asks for both again | `[!] Maximum salary cannot be lower than minimum salary. Please enter both again.` | `test_filters_recheck_the_salary_range` |
| `prompt_missing_data` | No skills typed when the AI found none | Validation: list is empty | Asks again | `[!] Please enter at least one skill.` | `test_missing_details_are_asked_at_the_console` |
| Any prompt | The keyboard is closed (input ends, `EOFError`) | Exception, **not** caught here | Raised to the app, which decides how to exit | Handled by the console app (added in a later commit) | `test_closed_keyboard_raises_eof_for_the_app_to_handle` |

## 2. Resume files (`IOManager`)

| Where | What can go wrong | Validation or exception | How it is handled | What the user sees | Test |
|---|---|---|---|---|---|
| `check_resume_file` | File doesn't exist | Validation: `os.path.exists` | Returns a message, file not opened | `File not found: <path>` | `test_a_missing_file_a_folder_and_a_wrong_type_are_each_a_message` |
| `check_resume_file` | Path is a folder | Validation: `os.path.isfile` | Returns a message | `That is a folder, not a file.` | `test_a_missing_file_a_folder_and_a_wrong_type_are_each_a_message` |
| `check_resume_file` | Not a PDF or `.docx` | Validation: extension in `ALLOWED_EXTENSIONS` | Returns a message | `'.txt' files are not supported. Please use a PDF or Word (.docx) file.` | `test_a_missing_file_a_folder_and_a_wrong_type_are_each_a_message` |
| `check_resume_file` | File is 0 bytes | Validation: `os.path.getsize` | Returns a message | `That file is empty.` | `test_an_empty_file_is_reported` |
| `read_resume` | File is damaged or isn't really a PDF/Word file, so `pypdf` or `python-docx` fails | Exception: `except Exception` (the two libraries raise many different error types) | Logs a warning with the error type, returns no text | The "not enough text" message below | `test_a_file_that_is_not_really_a_word_file_gives_no_text_and_no_crash` |
| `load_resume` | Under 100 characters of text (scanned image, password-protected, corrupted) | Validation: `len(text) < MIN_RESUME_CHARS` | Returns a message instead of the text | `Could not read enough text from that file. It may be a scanned image, password-protected or corrupted. Please try another file.` | `test_a_resume_with_too_little_text_is_rejected` |
| `list_resumes` | Resumes folder missing, or can't be created or read | Exception: `except OSError` | Creates the folder if it can; otherwise returns an empty list | `prompt_resume` shows where to put a resume and lets the user check again or quit (`q`) | `test_list_resumes_shows_only_pdf_and_docx_and_survives_a_missing_folder` |
| `unused_resume_path` | An upload has the same name as a saved resume | Validation: `os.path.exists` | Picks `resume_2.pdf`, `resume_3.pdf`, ... instead | Nothing: the old resume is kept | `test_an_upload_never_replaces_a_saved_resume` |

## 3. Web form values (`IOManager`)

The web pages can't ask again, so these methods return `(result, problem)` and the page shows `problem`.

| Where | What can go wrong | Validation or exception | How it is handled | What the user sees | Test |
|---|---|---|---|---|---|
| `resume_path_from_form` | Uploaded file isn't a PDF or `.docx` | Validation: extension check | Returns a problem, nothing saved | `Please upload a PDF or Word (.docx) file.` | `test_the_web_form_only_uses_a_listed_resume_or_a_new_upload` |
| `resume_path_from_form` | No upload and the chosen name isn't a listed resume | Validation: name must be in `list_resumes()` | Returns a problem | `Please choose a resume or upload one.` | `test_the_web_form_only_uses_a_listed_resume_or_a_new_upload` |
| `check_filters` | Salary or years isn't plain digits (`lots`, `-5`, `+3_000`) | Validation: `isascii()` and `isdigit()` before `int()` | Returns a problem | `Minimum salary must be a whole number of 0 or more, e.g. 3000.` | `test_form_filters_get_a_message_instead_of_a_new_question` |
| `check_filters` | Number outside its range (years over 60) | Validation | Returns a problem | `Maximum years of experience must be between 0 and 60 years.` | `test_form_filters_get_a_message_instead_of_a_new_question` |
| `check_filters` | Maximum salary lower than minimum | Validation | Returns a problem | `Maximum salary cannot be lower than minimum salary.` | `test_form_filters_get_a_message_instead_of_a_new_question` |
| `check_filters` | Job type or work arrangement not in the lists | Validation | Returns a problem | `Please pick a job type and work arrangement from the lists.` | `test_form_filters_get_a_message_instead_of_a_new_question` |
| `fill_missing_data` | Qualification typed into the form isn't a known level | Validation: must be in `EDUCATION_LEVELS` | Ignored, the profile is left as it was | Nothing | `test_form_fields_never_overwrite_what_the_ai_found_or_accept_unknown_levels` |

## 4. Logging and saved files (`IOManager`)

| Where | What can go wrong | Validation or exception | How it is handled | What the user sees | Test |
|---|---|---|---|---|---|
| `set_up_logging` | Log folder or `logs/job_matcher.log` can't be created (e.g. read-only folder) | Exception: `except OSError` | Warns once and keeps logging to the terminal only | `Could not open the log file ... logging to the terminal only` | Not tested yet |
| `save_tailored_resume` | Tailored Word file can't be written | Exception: `OSError` is **raised** on purpose | The caller (tailoring) decides what to tell the user | Added with the tailoring rows | Not tested yet |

## 5. Word files (`DocxEditor`)

`DocxEditor` doesn't catch errors itself; a broken Word file raises the library's error to the caller.

| Where | What can go wrong | Validation or exception | How it is handled | What the user sees | Test |
|---|---|---|---|---|---|
| `read_docx_paragraphs`, `write_edited_docx` | The resume isn't a readable Word file | Exception raised to the caller | Handled by tailoring (added with the tailoring rows) | Added with the tailoring rows | Not tested yet |
| `write_edited_docx` | An edit for a paragraph whose text is all inside a link (no text runs) | Not checked: `runs[0]` would raise `IndexError` | Prevented before it gets here: `read_docx_paragraphs` marks these paragraphs `editable: False`, and `ground_docx_edits` (logic layer) drops any AI edit for a paragraph that isn't editable | Nothing | Not tested yet (raised in the review of PR #13) |

## Gaps to look at

- `set_up_logging`, `save_tailored_resume` and the two `DocxEditor` rows have no tests yet.
- `read_resume` catches every `Exception`, which is broad. It is kept because `pypdf` and `python-docx` raise many
  different error types for a damaged file, and every one of them should become the same "not enough text" message.
