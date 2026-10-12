/* Route A browser-side extractor — stage D1.
 *
 * Runs in the user's own logged-in Amazon session, inside the built-in browser
 * pane, and returns one capture payload for one review page. Paste it into the
 * browser's JavaScript evaluator; `collect_reviews.py extractor` prints it so
 * the text that runs is always the text in this file.
 *
 *
 * WHY THIS FILE EXISTS RATHER THAN THE MODEL READING THE PAGE
 * -----------------------------------------------------------
 * CORE-PROMPT §1.3: review text is collected verbatim or it does not exist, and
 * a model-written review entering the corpus is unrecoverable. The assistant is
 * the transport between this page and the disk, and a transport that can
 * paraphrase is a transport that eventually will.
 *
 * So the payload carries `payload_sha256`, computed HERE, over the review text
 * as it stood in the DOM. `collect_reviews.py capture` recomputes it over what
 * actually reached disk and refuses the batch on any mismatch. One altered
 * character — a smoothed typo, a trimmed sentence, a re-wrapped line — fails
 * the batch. The rule stops being a promise and becomes a check.
 *
 * The hash covers the fields that carry meaning (ids, text, stars, dates), not
 * the whole object: see HASH_HEADER / HASH_REVIEW below. Length-prefixed
 * encoding, not JSON, because JSON.stringify and Python's json.dumps disagree
 * about escaping lone surrogates and U+2028, and a hash scheme that disagrees
 * across languages fails open on exactly the reviews with unusual characters.
 *
 *
 * WHAT IT DOES NOT DO
 * -------------------
 * It does not page, click, scroll or retry. One call reads one page that the
 * session already has open. Pacing, and the decision to turn a page at all,
 * stay with the operator (CORE-PROMPT §3: paced like reading, not scraping).
 *
 * It does not work around a block. If the page is a sign-in redirect, a CAPTCHA
 * or a throttle, it returns {blocked: true} with the reason and no reviews, and
 * the collector records the block and stops (CORE-PROMPT §9).
 */

(async () => {
  "use strict";

  const CAPTURE_VERSION = "1";

  /* ---------------------------------------------------------------- blocks */

  /* Detected from the page itself, never from a status code we cannot see.
   * Ordered most-specific first: a CAPTCHA served under /ap/signin is a
   * CAPTCHA, not a sign-in. */
  function detectBlock() {
    const url = location.href;
    const body = (document.body ? document.body.innerText : "") || "";
    const head = body.slice(0, 4000);

    if (/Enter the characters you see below/i.test(head) ||
        document.querySelector("form[action*='validateCaptcha']") ||
        document.querySelector("#captchacharacters")) {
      return "captcha";
    }
    if (/not a robot|Type the characters you see in this image/i.test(head)) {
      return "bot_challenge";
    }
    if (/\/ap\/signin/.test(url) || /\/ap\/cvf\//.test(url)) {
      return "signin_required";
    }
    if (/Sorry, we just need to make sure you're not a robot/i.test(head)) {
      return "bot_challenge";
    }
    if (/To discuss automated access to Amazon data please contact/i.test(head)) {
      return "automated_access_notice";
    }
    if (/Request was throttled|Slow down|too many requests/i.test(head)) {
      return "rate_limited";
    }
    /* The 503 "dogs of Amazon" page. */
    if (/\/errors\/validateCaptcha/.test(url) ||
        (/Sorry! Something went wrong/i.test(head) && !document.querySelector("[data-hook='review']"))) {
      return "error_page";
    }
    return null;
  }

  /* ------------------------------------------------------------ page facts */

  function digits(s) {
    if (!s) return null;
    /* First run of digits with , . or spaces as separators: "8,672 global
     * ratings" -> 8672. Returns null rather than 0 when absent, because a
     * coverage ratio divided by a guessed 0 is worse than no ratio. */
    const m = String(s).replace(/ /g, " ").match(/([0-9][0-9.,\s]*)/);
    if (!m) return null;
    const n = parseInt(m[1].replace(/[^0-9]/g, ""), 10);
    return isNaN(n) ? null : n;
  }

  function txt(sel, root) {
    const e = (root || document).querySelector(sel);
    return e ? e.textContent.replace(/\s+/g, " ").trim() : null;
  }

  /* The denominator of the coverage ratio (CORE-PROMPT §4).
   *
   * Amazon states two different totals and they are not interchangeable:
   * "8,672 global ratings" counts stars left with or without text, while
   * "1,234 global reviews" counts the ones with text — the population this
   * pipeline can actually sample. Both are captured and the collector records
   * which one it divided by, because a ratio over the larger number understates
   * coverage and a ratio whose denominator is unlabelled means nothing. */
  function claimedTotals() {
    const out = {
      ratings_total: null,
      reviews_total: null,
      ratings_source_text: null,
      reviews_source_text: null,
      average_rating_text: null
    };

    const totalHook = txt("[data-hook='total-review-count']");
    const cmpsTotal = txt("[data-hook='cr-filter-info-review-rating-count']");
    const acr = txt("#acrCustomerReviewLink");

    /* "1,234 global ratings, 567 with reviews" on the reviews page. */
    if (cmpsTotal) {
      const parts = cmpsTotal.split(",").join("").match(/([0-9]+)\s*total ratings[^0-9]*([0-9]+)\s*with reviews/i);
      if (parts) {
        out.ratings_total = parseInt(parts[1], 10);
        out.reviews_total = parseInt(parts[2], 10);
        out.ratings_source_text = cmpsTotal;
        out.reviews_source_text = cmpsTotal;
      }
    }
    if (out.ratings_total === null && totalHook) {
      out.ratings_total = digits(totalHook);
      out.ratings_source_text = totalHook;
      if (/global reviews?/i.test(totalHook) && !/ratings?/i.test(totalHook)) {
        out.reviews_total = out.ratings_total;
        out.ratings_total = null;
        out.reviews_source_text = totalHook;
        out.ratings_source_text = null;
      }
    }
    if (out.ratings_total === null && acr) {
      out.ratings_total = digits(acr);
      out.ratings_source_text = acr;
    }
    out.average_rating_text = txt("[data-hook='rating-out-of-text']") ||
                              txt("[data-hook='average-star-rating']");
    return out;
  }

  /* Sort order as the PAGE reports it, never as the URL asked.
   *
   * A URL carrying sortBy=recent that silently fell back to helpfulness would
   * otherwise be recorded as a recency sample, and §4 says a helpfulness-ranked
   * series partly measures Amazon's ranking algorithm. The collector compares
   * this against what was requested and refuses a batch where they disagree. */
  function observedSort() {
    const sel = document.querySelector("#sort-order-dropdown, select[name='sortBy']");
    if (sel) {
      const opt = sel.options ? sel.options[sel.selectedIndex] : null;
      if (opt) return { value: sel.value || null, label: opt.textContent.trim() };
      return { value: sel.value || null, label: null };
    }
    /* The dropdown is rendered as an a-dropdown button on most pages. */
    const prompt = document.querySelector("#sort-order-dropdown + .a-button-dropdown .a-dropdown-prompt, " +
                                          "[data-action='a-dropdown-button'] .a-dropdown-prompt");
    if (prompt) return { value: null, label: prompt.textContent.trim() };
    const u = new URL(location.href);
    return { value: u.searchParams.get("sortBy"), label: null, from_url_only: true };
  }

  function observedFilters() {
    const u = new URL(location.href);
    const get = k => u.searchParams.get(k);
    return {
      /* filterByStar: one_star … five_star, all_stars, positive, critical */
      star: get("filterByStar") || "all_stars",
      media: get("mediaType") || "all_contents",
      format: get("formatType") || "all_formats",
      reviewer: get("reviewerType") || "all_reviews",
      language: get("filterByLanguage") || null,
      keyword: get("filterByKeyword") || null,
      page_number: parseInt(get("pageNumber") || "1", 10)
    };
  }

  /* --------------------------------------------------------------- reviews */

  /* Verbatim body text.
   *
   * `[data-hook="reviewText"]` also contains two screen-reader strings ("Brief
   * content visible, double tap…") and the Read more / Read less control. They
   * sit outside `.a-reactive-container`, so the inner container is preferred
   * over regex-stripping them off the text afterwards: a substitution applied
   * to verbatim text is a substitution that can hit a real review which happens
   * to quote the same words.
   *
   * innerText, not textContent, because it is layout-aware: it preserves the
   * paragraph breaks the reviewer typed, which textContent collapses away. */
  function bodyText(r) {
    const chain = [
      "[data-hook='reviewText'] .a-cardui-body .a-reactive-container",
      "[data-hook='reviewText'] .a-reactive-container",
      "[data-hook='review-body'] span.review-text-content span",
      "[data-hook='review-body'] span",
      "[data-hook='review-body']",
      "[data-hook='reviewText']"
    ];
    for (const sel of chain) {
      const e = r.querySelector(sel);
      if (e) {
        const t = (e.innerText || "").trim();
        if (t) return { text: t, via: sel };
      }
    }
    return { text: "", via: null };
  }

  function titleText(r) {
    const e = r.querySelector("[data-hook='review-title'], [data-hook='reviewTitle']");
    if (!e) return "";
    /* On the reviews page the title node also holds the star-rating text as a
     * hidden span ("5.0 out of 5 stars"). Taking the last non-empty child span
     * drops it; falling back to the node's own text keeps the title when the
     * markup has no span at all. */
    const spans = Array.from(e.querySelectorAll("span"))
      .map(s => (s.textContent || "").trim())
      .filter(t => t && !/out of 5 stars?$/i.test(t));
    if (spans.length) return spans[spans.length - 1];
    return (e.innerText || e.textContent || "").trim();
  }

  function starRating(r) {
    const e = r.querySelector("[data-hook='review-star-rating'], [data-hook='cmps-review-star-rating']");
    const s = e ? (e.textContent || "").trim() : "";
    const m = s.match(/([0-9]+(?:\.[0-9]+)?)\s*out of\s*5/i);
    return { rating: m ? parseFloat(m[1]) : null, text: s || null };
  }

  /* "Reviewed in the United States on December 20, 2022" — the country matters.
   * amazon.com shows reviews written in other marketplaces, and a series that
   * mixes them is a series over a changing population. Parsed in Python; the
   * raw string is carried so the parse can be re-checked later. */
  function dateParts(r) {
    const e = r.querySelector("[data-hook='review-date']");
    const s = e ? (e.textContent || "").replace(/\s+/g, " ").trim() : "";
    const m = s.match(/^Reviewed in (.+?) on (.+)$/i);
    return { text: s || null, country: m ? m[1].trim() : null, date_text: m ? m[2].trim() : null };
  }

  function helpfulVotes(r) {
    const e = r.querySelector("[data-hook='helpful-vote-statement']");
    const s = e ? (e.textContent || "").replace(/\s+/g, " ").trim() : "";
    if (!s) return { votes: 0, text: null, stated: false };
    /* "One person found this helpful" has no digit. */
    if (/^one person/i.test(s)) return { votes: 1, text: s, stated: true };
    const n = digits(s);
    return { votes: n === null ? 0 : n, text: s, stated: n !== null };
  }

  function reviewId(r) {
    /* Amazon's own review id (RHBCYOKA8617N). Strictly better than a hash of
     * user + asin + date: it is the identifier Amazon itself dedupes on, it is
     * stable across pages and sorts, and it survives the reviewer renaming
     * their profile. Absence is reported, not papered over — the collector
     * assigns a fallback id and records that it did. */
    if (r.id && /^R[A-Z0-9]{8,}$/.test(r.id)) return r.id;
    const a = r.querySelector("a[href*='/gp/customer-reviews/']");
    if (a) {
      const m = a.getAttribute("href").match(/\/gp\/customer-reviews\/([A-Z0-9]+)/);
      if (m) return m[1];
    }
    return null;
  }

  function extractOne(r) {
    const b = bodyText(r);
    const st = starRating(r);
    const d = dateParts(r);
    const h = helpfulVotes(r);
    const prof = r.querySelector("a.a-profile");
    return {
      review_id: reviewId(r),
      star_rating: st.rating,
      star_rating_text: st.text,
      review_date_text: d.date_text,
      review_date_raw: d.text,
      review_country: d.country,
      title: titleText(r),
      text: b.text,
      text_via: b.via,
      verified_purchase: !!r.querySelector("[data-hook='avp-badge']"),
      vine_voice: /vine/i.test((txt("[data-hook='review-badges']", r) || "")),
      helpful_votes: h.votes,
      helpful_votes_text: h.text,
      helpful_votes_stated: h.stated,
      variation: txt("[data-hook='format-strip'], [data-hook='format-strip-linkless']", r),
      profile_name: txt(".a-profile-name", r),
      /* The reviewer's profile URL is deliberately NOT captured. It identifies
       * a person, it is not needed for tone, and a corpus that carries it is a
       * corpus that has to be defended. The anonymised handle is enough to see
       * one reviewer dominating a quarter. */
      profile_id_present: !!(prof && prof.getAttribute("href"))
    };
  }

  /* ------------------------------------------------------------------ hash */

  const HASH_HEADER = ["capture_version", "marketplace", "product_id", "page_number"];
  const HASH_REVIEW = ["review_id", "star_rating", "review_date_text", "review_country",
                       "title", "text", "verified_purchase", "helpful_votes",
                       "variation", "profile_name"];

  const enc = new TextEncoder();

  /* name + "=" + <utf8 byte length> + ":" + <utf8 bytes>, concatenated.
   *
   * Length-prefixed and so unambiguous whatever the review contains: no
   * delimiter a reviewer could type, no escaping, and byte lengths from
   * TextEncoder rather than String.length, which counts UTF-16 units and would
   * disagree with Python on every emoji. */
  function canonParts(obj, fields) {
    const parts = [];
    for (const f of fields) {
      let v = obj[f];
      if (v === null || v === undefined) v = "";
      else if (v === true) v = "true";
      else if (v === false) v = "false";
      else v = String(v);
      const vb = enc.encode(v);
      parts.push(enc.encode(f + "=" + vb.length + ":"), vb);
    }
    return parts;
  }

  async function payloadHash(header, reviews) {
    const parts = canonParts(header, HASH_HEADER);
    for (const r of reviews) {
      for (const p of canonParts(r, HASH_REVIEW)) parts.push(p);
    }
    let n = 0;
    for (const p of parts) n += p.length;
    const buf = new Uint8Array(n);
    let o = 0;
    for (const p of parts) { buf.set(p, o); o += p.length; }
    const h = await crypto.subtle.digest("SHA-256", buf);
    return Array.from(new Uint8Array(h)).map(x => x.toString(16).padStart(2, "0")).join("");
  }

  /* ------------------------------------------------------------------ main */

  const blocked = detectBlock();
  const u = new URL(location.href);
  const filters = observedFilters();
  const productId = (u.pathname.match(/\/(?:product-reviews|dp|gp\/product)\/([A-Z0-9]{10})/) || [])[1] ||
                    (document.querySelector("input#ASIN, input[name='ASIN']") || {}).value || null;

  const header = {
    capture_version: CAPTURE_VERSION,
    marketplace: location.hostname.replace(/^www\./, ""),
    product_id: productId,
    page_number: filters.page_number
  };

  if (blocked) {
    /* No reviews, no hash over content that does not exist. A block is a
     * finding (CORE-PROMPT §9) and the collector writes it to the batch log. */
    return {
      capture_version: CAPTURE_VERSION,
      blocked: true,
      block_reason: blocked,
      collected_at: new Date().toISOString(),
      marketplace: header.marketplace,
      product_id: productId,
      /* Every field the hash covers is emitted at the payload's TOP LEVEL,
       * where the verifier looks for it. page_number also appears under
       * `filters`, and relying on that nesting alone was a real bug: the
       * verifier read it as absent, hashed an empty string where the browser
       * had hashed "1", and every capture failed by exactly one byte. A hash
       * over a field the verifier cannot find is a hash that always fails. */
      page_number: header.page_number,
      page_url: location.href,
      page_title: document.title,
      sort_order_observed: null,
      filters: filters,
      claimed: claimedTotals(),
      returned: 0,
      reviews: [],
      payload_sha256: null
    };
  }

  const nodes = Array.from(document.querySelectorAll("[data-hook='review']"));
  const reviews = nodes.map(extractOne);
  const hash = await payloadHash(header, reviews);

  return {
    capture_version: CAPTURE_VERSION,
    blocked: false,
    block_reason: null,
    collected_at: new Date().toISOString(),
    marketplace: header.marketplace,
    product_id: productId,
    page_number: header.page_number,
    page_url: location.href,
    page_title: document.title,
    sort_order_observed: observedSort(),
    filters: filters,
    claimed: claimedTotals(),
    returned: reviews.length,
    reviews: reviews,
    payload_sha256: hash
  };
})()
