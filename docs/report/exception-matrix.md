# Exception handling matrix

> Draft. This version covers the input/output layer (`src/io_layer/`), the job search (`SearchService`) and resume
> tailoring (`TailoringService`). The console and web app rows are added in later commits.

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
| `save_tailored_resume` | Tailored Word file can't be written | Exception: `OSError` is **raised** on purpose | The caller (the console app, which saves the file) decides what to tell the user | Added with the console app rows | Not tested yet |

## 5. Word files (`DocxEditor`)

`DocxEditor` doesn't catch errors itself; a broken Word file raises the library's error to the caller.

| Where | What can go wrong | Validation or exception | How it is handled | What the user sees | Test |
|---|---|---|---|---|---|
| `read_docx_paragraphs`, `write_edited_docx` | The resume isn't a readable Word file | Exception raised to the caller | Not caught by tailoring either (section 7): it reaches the console or web app | Added with the console and web app rows | Not tested yet |
| `write_edited_docx` | An edit for a paragraph whose text is all inside a link (no text runs) | Not checked: `runs[0]` would raise `IndexError` | Prevented before it gets here: `read_docx_paragraphs` marks these paragraphs `editable: False`, and `ground_docx_edits` (logic layer) drops any AI edit for a paragraph that isn't editable | Nothing | Not tested yet (raised in the review of PR #13) |

## 6. Job search (`SearchService`, through `JobPortal`, `AIManager` and `LogicManager`)

A search goes portal → AI → logic → storage. Each step gives back "nothing" (`None` or no answer) instead of
crashing, so a search that partly fails still shows what it found, with a message saying what is missing.

| Where | What can go wrong | Validation or exception | How it is handled | What the user sees | Test |
|---|---|---|---|---|---|
| `JobPortal.search` | No internet, a timeout or a server error (e.g. 500) | Exception: `except (requests.RequestException, ValueError)` | Tried 3 times, waiting 2 s then 4 s; then uses the listings this exact search returned last time, or gives `None` | If no listings at all: `Could not get any job listings from MyCareersFuture. Check your internet connection, or try again in a few minutes.` | `test_a_timeout_is_retried_and_can_then_succeed`, `test_a_portal_that_keeps_failing_gives_none_after_three_tries`, `test_the_last_listings_of_a_search_are_used_when_the_portal_is_down` |
| `JobPortal.search` | The reply isn't in the expected format | Validation: `raise ValueError` if `results` isn't a list, caught by the same `except` | Treated like a failed try (retried, then `None`) | As above | `test_a_reply_in_an_unexpected_format_is_a_failure_not_a_crash` |
| `JobPortal.search` | One listing in the reply is in a broken shape | Exception: `except (AttributeError, KeyError, TypeError)` | That listing is skipped and logged; the rest are kept | Nothing: the other listings are shown | `test_a_broken_listing_is_skipped_and_a_good_one_kept` |
| `JobPortal` | The file of saved listings is corrupt or can't be read | Exception: `except (OSError, ValueError)` | Ignored, logged as a warning | Nothing | `test_a_corrupt_listings_file_is_ignored` |
| `JobPortal.fetch_listings` | No listing fits all the user's preferences | Validation: each search step is tried in turn | Searches again with the job type only, then with no preferences (it doesn't widen the search if the portal failed) | `No jobs matched all your preferences, so these match your job type (Full Time) only.` | `test_fetch_listings_tries_fewer_preferences_when_nothing_fits`, `test_fetch_listings_stops_at_a_portal_failure_instead_of_widening_the_search` |
| `JobPortal.fetch_listings` | MyCareersFuture answers but lists nothing | Validation: an empty list is not counted as a failure | Returns `[]` | `MyCareersFuture has no listings for your resume's job titles and filters right now. Try loosening your filters, or another resume.` | `test_no_listings_is_an_empty_list_not_a_failure`, `test_partial_and_empty_search_messages` |
| `AIManager.run_task` | The AI's reply isn't valid JSON, or fails the schema check | Validation: `_parse` and `_validate` return `None` | Asked once more; `None` if the second reply is also unusable | Counted in the message below | `test_a_malformed_reply_is_asked_for_again_once`, `test_two_malformed_replies_give_none` |
| `AIManager.run_task` | The AI API call itself fails | Handled by the provider, which retries | Not asked again (the provider already retried); gives `None` | Counted in the message below | `test_a_failed_api_call_returns_none_without_asking_again` |
| `AIManager.extract_requirements_for_listings` | An unexpected error in one batch of 10 listings | Exception: `except Exception` around each batch | Only that batch's listings get no answer (`batches_failed` is counted); `LogicManager.add_requirements` leaves them out | `5 of 45 listings could not be analysed by the AI, so some matches may be missing. Search again later to fill the gaps.` | `test_a_failed_batch_leaves_its_listings_without_answers`, `test_partial_and_empty_search_messages` |
| `SearchService.find_jobs` | Listings were found but the AI couldn't analyse any | Validation: `jobs_analysed == 0` | Nothing to rank | `The AI could not analyse any of the 45 listings found. Check your API key, or wait a minute if the AI service is busy.` | `test_partial_and_empty_search_messages` |
| `SearchService.save` | The saved-jobs store can't be written (locked or full disk); `DataManager.save_new_jobs` raises `OSError` | Exception: `except Exception` | Logs the error and returns `None`; `rank_and_save` sets `save_failed: True` and still returns the jobs | The results are still shown (the save-failed message is added with the console and web app rows) | `test_an_unreachable_store_raises_an_os_error_the_apps_already_handle` (DataManager); `SearchService` itself not tested yet |

## 7. Resume tailoring (`TailoringService`, `ResumeTailor` and `TailoringGuard`)

| Where | What can go wrong | Validation or exception | How it is handled | What the user sees | Test |
|---|---|---|---|---|---|
| `JobPortal.details` | The job's full description can't be fetched (no internet, a bad link, an odd reply) | Exception: `except (requests.RequestException, ValueError, AttributeError, KeyError, TypeError)`; validation: the link must end in a 32-character job ID | Returns `None`; `TailoringService` tailors from the job's title and skills only and sets `had_description: False` | `The full job description could not be fetched, so it was tailored to the job's title and skills only.` | `test_details_need_a_real_job_link_and_survive_network_errors`, `test_job_details_in_an_unexpected_format_are_treated_like_no_reply` |
| `ResumeTailor.tailor` | The AI can't tailor the resume (no reply, or unusable twice) | Validation: `_ask_ai` returns `None` | Nothing is checked or written; `TailoringService.tailor` returns `None` | Added with the console and web app rows | `test_when_the_ai_fails_there_is_nothing_to_check` |
| `AIManager.tailor_resume`, `tailor_docx` | The resume is very long | Validation: length over `MAX_RESUME_CHARS` | Only the start is sent, with a warning in the log | Nothing | `test_a_very_long_resume_is_cut_before_it_is_sent`, `test_tailor_docx_cuts_a_very_long_resume` |
| `TailoringGuard` (through `LogicManager`) | The AI adds a skill, tool or number that isn't in the resume | Validation: every change is checked against the original resume | The change is left out or the original wording is kept | e.g. `Left out "Data Analyst Intern": it isn't in your resume.` | `test_the_guard_blocks_spelling_variants_of_a_missing_skill`, `test_the_guard_blocks_common_tools_the_job_does_not_list` |
| `LogicManager.keep_bullet_groups` | The AI removes every point under one heading | Validation | Every point under that heading is kept | `Kept every point under "Projects": removing them all would leave it empty.` | `test_keep_bullet_groups_never_empties_a_heading` |
| `LogicManager.fit_edits_to_page_count` | New wording is longer and could add a page to a Word resume | Validation: line count | The original wording is kept for that paragraph | `Kept "Built dashboards for..." as it was: the new wording was longer and could push your resume onto another page.` | `test_fit_edits_to_page_count_keeps_the_number_of_lines` |
| `TailoringService.tailor` | A resume type no tailor expects | Validation: `handles()` | `PdfResumeTailor` takes any resume and is tried last, so there is always a tailor | Nothing | `test_each_tailor_says_which_resumes_it_handles` |
| `TailoringService.tailor` | The Word resume is damaged (`read_docx_paragraphs` or `write_edited_docx` raises) | Exception, **not** caught here | Raised to the console or web app | Added with the console and web app rows | Not tested yet |
| `TailoringService.tailor` | The user cancels on the web page (`on_progress` raises `InterruptedError`) | Exception, **not** caught here on purpose | Raised to the web app, which stops the task | Added with the web app rows | Not tested yet |

## Gaps to look at

- `set_up_logging`, `save_tailored_resume` and the two `DocxEditor` rows have no tests yet.
- `SearchService` and `TailoringService` have no tests of their own yet (both were checked by hand in their reviews,
  PR #31 and PR #36).
- `SearchService.save` catches every `Exception`, but `DataManager.save_new_jobs` only documents `OSError`, so
  `except OSError` would be more specific.
- `read_resume` catches every `Exception`, which is broad. It is kept because `pypdf` and `python-docx` raise many
  different error types for a damaged file, and every one of them should become the same "not enough text" message.
