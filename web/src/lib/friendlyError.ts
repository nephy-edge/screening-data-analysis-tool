/**
 * Turn a raw engine/backend error message into a short, plain-language line
 * the user can act on. The API layer throws `err.message` taken from
 * FastAPI's `{"detail": "..."}` (or a generic "Engine request failed (NNN)"),
 * which is often a python traceback or cryptic wording. Known patterns are
 * mapped to a friendly version; anything unrecognised gets a generic line with
 * the raw text kept as a parenthetical so it's not lost.
 */
export function friendlyError(raw: string | null | undefined): string {
  const m = raw ?? '';

  if (/Analysis failed/i.test(m)) {
    return 'We couldn’t complete the analysis. Check that every column is mapped and the file has the expected loan fields, then try again.';
  }
  if (/Missing required template fields in your mapping/i.test(m)) {
    return 'Some required columns weren’t mapped. Go back to the column mapping screen, map them all, then run the analysis again.';
  }
  if (/Missing required rental fields in your mapping/i.test(m)) {
    return 'Some required columns weren’t mapped. Go back to the column mapping screen, map them all, then run the analysis again.';
  }
  if (/Mapping references missing source columns/i.test(m)) {
    return 'One or more mapped columns can’t be found in your file. Update the column mapping and try again.';
  }
  if (/Missing required .* fields in payload rows/i.test(m)) {
    return 'Your data is missing some required fields. Check the file has the expected columns and try again.';
  }
  if (/No rows provided/i.test(m) || /File parsed with no rows/i.test(m) || /came back empty/i.test(m)) {
    return 'We couldn’t find any data rows. Make sure your file isn’t empty and try again.';
  }
  if (/ingestId not found|upload may have expired/i.test(m)) {
    return 'That upload is no longer available. Please upload the file again.';
  }
  if (/Engine request failed/i.test(m)) {
    return 'Something went wrong running the analysis. Please try again — if it keeps failing, your file may use a format we don’t fully support yet.';
  }
  return `Something went wrong${m ? ` (${m})` : ''}. Please try again.`;
}
