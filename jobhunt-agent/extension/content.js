// Content script: read the job posting off the page the user is looking at.
// Site-specific selectors first, then a generic fallback. Reads only — it never
// clicks, submits, or automates anything on the user's account.

function txt(el) { return (el && el.textContent || "").replace(/\s+/g, " ").trim(); }
function firstText(selectors) {
  for (const s of selectors) {
    const el = document.querySelector(s);
    if (el && txt(el)) return txt(el);
  }
  return "";
}

function extractJob() {
  const host = location.hostname;
  let title = "", company = "", jd = "";

  if (host.includes("greenhouse.io")) {
    title = firstText([".app-title", "h1.section-header", "h1"]);
    company = firstText([".company-name", "span.company-name"]);
    jd = firstText(["#content", ".job__description", ".content"]);
  } else if (host.includes("lever.co")) {
    title = firstText([".posting-headline h2", "h2"]);
    company = firstText([".main-header-logo img"]) ||
              (document.querySelector(".main-header-logo img")?.alt || "");
    jd = firstText([".section-wrapper .section", ".content"]);
  } else if (host.includes("ashbyhq.com")) {
    title = firstText(["h1", '[class*="jobPostingHeader"] h1']);
    jd = firstText(['[class*="jobPosting"] [class*="description"]', "main"]);
  } else if (host.includes("linkedin.com")) {
    title = firstText([".job-details-jobs-unified-top-card__job-title", "h1"]);
    company = firstText([".job-details-jobs-unified-top-card__company-name a",
                         ".job-details-jobs-unified-top-card__company-name"]);
    jd = firstText([".jobs-description__content", "#job-details", ".jobs-box__html-content"]);
  } else if (host.includes("indeed.com")) {
    title = firstText(['[data-testid="jobsearch-JobInfoHeader-title"]', "h1"]);
    company = firstText(['[data-testid="inlineHeader-companyName"]', '[data-company-name]']);
    jd = firstText(["#jobDescriptionText"]);
  } else if (host.includes("wellfound.com") || host.includes("otta.com")) {
    title = firstText(["h1"]);
    jd = firstText(["main", '[class*="description"]', "article"]);
  }

  // generic fallback for anything we didn't special-case
  if (!title) title = firstText(["h1"]) || document.title;
  if (!jd) jd = firstText(["main", "article"]) || txt(document.body).slice(0, 6000);

  return { title, company, jd: jd.slice(0, 8000), url: location.href };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "EXTRACT_JOB") {
    try { sendResponse(extractJob()); }
    catch (e) { sendResponse({ error: String(e) }); }
  }
  return true; // async
});
