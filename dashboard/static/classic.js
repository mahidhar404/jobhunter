// FROZEN — redirected to Ops (/). UI-033: not a live UI; kept on disk only.
const STAGES = ["discovered", "tailoring", "navigating", "filling", "ready_for_review", "applied"];
// temporary UI override — remove when user says undo
const TEMP_APPLIED_COUNT_OVERRIDE = null;
const STATUS_COLORS = {
  discovered: "#7a828c",
  tailoring: "#e8913a",
  navigating: "#e8913a",
  filling: "#e8913a",
  resuming: "#e8913a",
  stuck: "#e8913a",
  blocked_captcha: "#e8913a",
  ready_for_review: "#3dbf8a",
  applied: "#3dbf8a",
  cancelled: "#7a828c",
  skipped_manual: "#7a828c",
  skipped_duplicate: "#7a828c",
  skipped_contract: "#7a828c",
  skipped_easy_apply: "#7a828c",
  deleted: "#e05555",
};
/** Soft-delete reason codes → short (list/group) + long (dossier) labels. */
const DELETED_REASON_LABELS = {
  excessive_yoe: { short: "Excessive YOE", long: "Excessive experience / YOE too high" },
  citizenship_or_greencard: { short: "Citizenship / GC", long: "Citizenship / green card required" },
  clearance: { short: "Clearance", long: "Security clearance" },
  clearance_or_intel: { short: "Clearance", long: "Security clearance" },
  management_track: { short: "Management / seniority", long: "Management / seniority" },
  seniority: { short: "Management / seniority", long: "Management / seniority" },
  non_us: { short: "Non-US location", long: "Non-US location" },
  non_us_location: { short: "Non-US location", long: "Non-US location" },
  user: { short: "Deleted by you", long: "Deleted by you" },
  manual: { short: "Deleted by you", long: "Deleted by you" },
};
const DELETED_REASON_ORDER = [
  "excessive_yoe",
  "citizenship_or_greencard",
  "clearance_or_intel",
  "management_track",
  "non_us_location",
  "user",
  "",
];
const TERMINAL = ["applied", "skipped_duplicate", "skipped_contract", "skipped_easy_apply", "cancelled", "skipped_manual"];
// Most urgent first - used to pick the single indicator dot a collapsed
// company group shows, and to decide which groups float to the top of
// the list (see groupPriorityStatus/render()).
const PRIORITY_ORDER = [
  "stuck", "blocked_captcha", "filling", "navigating", "tailoring", "resuming",
  "ready_for_review", "discovered", "applied",
  "cancelled", "skipped_manual", "skipped_duplicate", "skipped_contract", "skipped_easy_apply",
];
const IN_PROGRESS_OR_NEEDS_ATTENTION = ["tailoring", "navigating", "filling", "resuming", "stuck", "blocked_captcha"];
// Management-track / above-senior + clearly non-US locations + clearance /
// intel-agency roles — hidden from a fresh, never-touched "discovered"
// listing. New discoveries are already blocked at the source (see
// scripts/discovery_filters.py via dedup_listings.py); this catches
// anything already sitting in jobs.json from before those rules existed.
// Keep patterns in sync with discovery_filters.py. Scoped to
// status === "discovered" only — once you've engaged with a job, it must
// never disappear just because of title/location/clearance heuristics
// (real example: "Sr. Lead Machine Learning Engineer (IC)" matches "lead"
// but had real completed work behind it).
// Keep in sync with scripts/discovery_filters.py (single source of policy).
const SENIORITY_EXCLUDE_RE = /\b(principal|staff|lead|manager|mgr|director|vp|svp|evp|vice[\s-]+president|head\s+of|chief|founder|partner|fellow|distinguished|supervisor|architect|cto|ceo|cpo|cfo|coo|cio)\b/i;
const NON_US_LOCATION_RE = /\b(india|japan|china|singapore|philippines|germany|france|poland|mexico|brazil|australia|vietnam|indonesia|malaysia|thailand|canada|united\s+kingdom|\buk\b|england|scotland|ireland|wales|netherlands|spain|italy|sweden|norway|denmark|switzerland|belgium|portugal|austria|finland|israel|south\s+korea|\bkorea\b|taiwan|hong\s+kong|dubai|u\.?a\.?e\.?|united\s+arab\s+emirates|new\s+zealand|argentina|colombia|chile|peru|ecuador|bolivia|uruguay|paraguay|venezuela|guatemala|honduras|nicaragua|costa\s+rica|panama|dominican\s+republic|saudi\s+arabia|\bksa\b|qatar|kuwait|bahrain|oman|jordan|lebanon|egypt|morocco|tunisia|nigeria|kenya|ghana|ethiopia|south\s+africa|ukraine|romania|serbia|slovakia|slovenia|croatia|hungary|czech(\s+republic)?|\bczechia\b|bulgaria|lithuania|latvia|estonia|greece|turkey|turkiye|pakistan|bangladesh|sri\s+lanka|nepal|cambodia|myanmar|armenia|azerbaijan|kazakhstan|uzbekistan|tajikistan|north\s+macedonia|macedonia|belarus|moldova|europe|european(\s+union)?|emea|apac|latam|\basia\b|africa|middle\s+east|worldwide|\bglobal\b|ontario|quebec|alberta|manitoba|saskatchewan|british\s+columbia|nova\s+scotia|new\s+brunswick|newfoundland|prince\s+edward|karnataka|telangana|maharashtra|tamil\s+nadu|kerala|gujarat|haryana|uttar\s+pradesh|west\s+bengal|andhra\s+pradesh|rajasthan|madhya\s+pradesh|odisha|assam|jharkhand|bangalore|bengaluru|mumbai|delhi|hyderabad|pune|chennai|kolkata|gurgaon|gurugram|noida|ahmedabad|jaipur|coimbatore|kochi|thiruvananthapuram|trivandrum|indore|bhubaneswar|vadodara|nagpur|mysuru|visakhapatnam|lucknow|chandigarh|kuala\s+lumpur|penang|bangkok|hanoi|ho\s+chi\s+minh|istanbul|athens|zagreb|gdansk|wroclaw|tokyo|osaka|shanghai|beijing|shenzhen|manila|jakarta|toronto|vancouver|montreal|ottawa|calgary|edmonton|kitchener|kitchener-waterloo|mississauga|winnipeg|halifax|london|paris|munich|berlin|amsterdam|dublin|zurich|geneva|stockholm|copenhagen|oslo|helsinki|lisbon|madrid|barcelona|rome|milan|prague|budapest|vienna|brussels|warsaw|krakow|bucharest|sofia|belgrade|bratislava|vilnius|tallinn|riga|edinburgh|glasgow|melbourne|sydney|brisbane|perth|adelaide|auckland|wellington|seoul|taipei|tel\s+aviv|jerusalem|haifa|sao\s+paulo|rio\s+de\s+janeiro|bogota|medellin|santiago|buenos\s+aires|lima|quito|montevideo|mexico\s+city|ciudad\s+de\s+mexico|guadalajara|monterrey|dubai|abu\s+dhabi|doha|riyadh|jeddah|cape\s+town|johannesburg|lagos|nairobi|stuttgart|frankfurt|hamburg|cologne|dusseldorf|lyon|marseille|toulouse|lille|\bgbr\b|\bcan\b|\bind\b|\baus\b|\bdeu\b|\bfra\b|\bnld\b|\bsgp\b|\birl\b|\bnzl\b|\bpol\b|\bmex\b|\bbra\b|\besp\b|\bita\b|\bswe\b|\bnor\b|\bdnk\b|\bche\b|\bbel\b|\bprt\b|\baut\b|\bfin\b|\bisr\b|\bkor\b|\btwn\b|\bphl\b|\bare\b|\brou\b|\buae\b|\bsau\b|\bqat\b)\b/i;
// u.s(?!\w) — not u.s. + \b; trailing "." is non-word so \b after u.s. never matches.
const US_LOCATION_STRONG_RE = /\b(united\s+states|u\.s\.a\.?|u\.s(?!\w)|\busa\b|\bus\b|remote[,\s\/-]*us|us[,\s\/-]*remote|us[-\s]?based|us[-\s]?only|alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new\s+hampshire|new\s+jersey|new\s+mexico|new\s+york|north\s+carolina|north\s+dakota|ohio|oklahoma|oregon|pennsylvania|rhode\s+island|south\s+carolina|south\s+dakota|tennessee|texas|utah|vermont|virginia|washington|west\s+virginia|wisconsin|wyoming|district\s+of\s+columbia|san\s+francisco|seattle|austin|boston|chicago|denver|atlanta|dallas|houston|miami|phoenix|portland|salt\s+lake|los\s+angeles|san\s+diego|san\s+jose|palo\s+alto|mountain\s+view|sunnyvale|redmond|bellevue|cupertino|menlo\s+park|foster\s+city|oakland|irvine|raleigh|durham|charlotte|nashville|minneapolis|pittsburgh|philadelphia|washington,\s*dc|new\s+york\s+city|\bnyc\b|bay\s+area|silicon\s+valley)\b/i;
// Bare ", XX" state abbreviations are a weaker US signal: ATS region codes
// collide with them ("Chennai, TN, in" - TN is Tamil Nadu). Ignored once an
// ISO-2 country tail resolves the location.
const US_STATE_ABBREV_RE = /,\s*(?:AL|AK|AZ|AR|CA|CO|CT|DC|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b/i;
const US_LOCATION_RE = new RegExp(
  `(?:${US_LOCATION_STRONG_RE.source}|${US_STATE_ABBREV_RE.source})`,
  "i",
);
// ATS location dumps append an ISO-3166 alpha-2 country ("Bengaluru, KA, in").
// Half of those codes collide with US state abbreviations, so the tail is
// resolved explicitly before the state-abbreviation signal runs.
const US_STATE_ABBREVS = new Set([
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI",
  "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
  "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
  "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
  "WV", "WI", "WY", "PR", "VI", "GU", "AS", "MP",
]);
const NON_US_ISO2_CODES = new Set([
  "ae", "ar", "at", "au", "bd", "be", "bg", "bh", "br", "by", "ca", "ch",
  "cl", "cn", "co", "cr", "cz", "de", "dk", "do", "eg", "es", "fi", "fr",
  "gb", "gr", "gt", "hk", "hr", "hu", "id", "ie", "il", "in", "it", "jo",
  "jp", "ke", "kr", "kw", "lk", "lt", "lu", "lv", "ma", "mx", "my", "ng",
  "nl", "no", "nz", "pa", "pe", "ph", "pk", "pl", "pt", "qa", "ro", "rs",
  "ru", "sa", "se", "sg", "si", "sk", "th", "tr", "tw", "ua", "uk", "uy",
  "ve", "vn", "za",
]);
// Clearance requirement language — not bare "security" / "secret" alone.
const CLEARANCE_REQUIREMENT_RE = /(\bts[\s_\/.\-]*sci\b|\btop[\s\-]*secret\b|\bpolygraph\b|\b(?:ci|full[\s\-]*scope)[\s\-]*poly(?:graph)?\b|\b(?:q|l)[\s\-]*clearance\b|\bdoe[\s\-]*(?:q|l)\b|\bdod[\s\-]*(?:secret|top[\s\-]*secret|ts|clearance)\b|\bsecret[\s\-]*clearance\b|\bsecurity[\s\-]*clearance\b|\bactive[\s\-]*(?:ts|sci|secret|top[\s\-]*secret|security)?[\s\-]*clearance\b|\b(?:ts|secret|top[\s\-]*secret)[\s\-]*cleared\b|\bcleared[\s\-]*(?:candidate|personnel|position|role|engineer|scientist)\b|\bclearance[\s\-]*(?:required|preferred|mandatory|needed|necessary|eligibility|level|requirements?)\b|\b(?:must|require[ds]?|required|need(?:s|ed)?|possess(?:es|ing)?|hold(?:s|ing)?|obtain(?:able|ing)?|eligible\s+for|ability\s+to\s+obtain|able\s+to\s+obtain|currently\s+(?:hold|have)|have\s+an?\s+active).{0,48}clearance\b|\bclearance.{0,24}(?:required|preferred|mandatory|needed)\b|\bclassified\s+(?:information|environment|program|material|data|systems?|networks?|work|facility|facilities)\b|\b(?:handle|access|process|work\s+(?:with|on))\s+classified\b|\bsci[\s\-]*clearance\b|\bsap(?:\/sar)?\s+clearance\b|\bclearance\s*:\s*(?:secret|top[\s\-]*secret|ts(?:[\s_\/.\-]*sci)?|sci|public\s+trust|(?:doe[\s\-]*)?[ql]|active)\b|\bclearance\s*:.{0,48}(?:obtain|eligible|public\s+trust|secret|ts[\s_\/.\-]*sci|polygraph)\b|\bclearance[\s\-]*(?:type|level)\s*:\s*(?:secret|top[\s\-]*secret|ts(?:[\s_\/.\-]*sci)?|sci|public\s+trust|(?:doe[\s\-]*)?[ql]|active|confidential)\b|\bclearance(?:[\s\-]*(?:required(?:\s+for\s+start)?|type|level))?\s*:?\s*(?:\u2026|\.\.\.)\s*\[\s*full\s+text\b|\(\s*public\s+trust\s*\)|\bpublic\s+trust\s+clearance\b|\b(?:must|require[ds]?|required|need(?:s|ed)?|possess(?:es|ing)?|hold(?:s|ing)?|obtain(?:able|ing)?|eligible\s+for|ability\s+to\s+obtain|able\s+to\s+obtain|currently\s+(?:hold|have)|have\s+an?\s+active|maintain(?:ing)?).{0,48}public\s+trust\b|\bpublic\s+trust(?:\s+clearance)?[\s\-]*(?:required|preferred|mandatory|needed)\b)/i;
// ATS "Clearance required: No/None" — stripped before CLEARANCE_REQUIREMENT_RE.
const CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE = /\bclearance[\s\-]*(?:required|preferred|mandatory|needed)(?:\s+for\s+start)?[\s:\-|*]*(?:no|none|n\/?a)\b/i;
const INTEL_AGENCY_COMPANY_RE = /(national\s+security\s+agency|\bnsa\b|central\s+intelligence(?:\s+agency)?|\bcia\b|defense\s+intelligence(?:\s+agency)?|\bdia\b|national\s+geospatial(?:[\s\-]+intelligence)?(?:\s+agency)?|\bnga\b|national\s+reconnaissance\s+office|\bnro\b|office\s+of\s+the\s+director\s+of\s+national\s+intelligence|\bodni\b|national\s+counterterrorism\s+center|\bnctc\b|defense\s+counterintelligence\s+and\s+security\s+agency|\bdcsa\b|intelligence\s+community\s+agency|u\.?s\.?\s+intelligence\s+community|\bic\s+agency\b)/i;
const INTEL_AGENCY_URL_RE = /(intelligencecareers\.gov|(?:^|[\.\/])nsa\.gov|(?:^|[\.\/])cia\.gov|(?:^|[\.\/])dia\.mil|(?:^|[\.\/])nga\.mil|(?:^|[\.\/])nro\.gov|(?:^|[\.\/])dni\.gov|(?:^|[\.\/])dcsa\.mil)/i;
const STALE_LISTING_MAX_AGE_DAYS = 30;

function foldAccents(s) {
  try {
    return String(s).normalize("NFKD").replace(/\p{M}/gu, "");
  } catch {
    return String(s);
  }
}

function isExcludedTitle(title) {
  const t = title || "";
  return SENIORITY_EXCLUDE_RE.test(t);
}

function isClearlyNonUsLocation(location) {
  const loc = foldAccents(location || "").trim();
  if (!loc) return false; // undetermined — keep
  const parts = loc.split(",").map((p) => p.trim());
  const tail = parts.length >= 2 && /^[A-Za-z]{2}$/.test(parts[parts.length - 1])
    ? parts[parts.length - 1]
    : null;
  if (tail) {
    const code = tail.toLowerCase();
    if (["us", "pr", "vi", "gu"].includes(code)) return false;
    if (NON_US_ISO2_CODES.has(code)) {
      const head = parts.slice(0, -1).join(", ");
      const collidesWithState = US_STATE_ABBREVS.has(tail.toUpperCase());
      // Unambiguous codes (my, gb, sg, …) decide alone; codes that double as
      // state abbreviations need the lowercase ATS spelling plus corroboration,
      // so "Dublin, CA" and "Indianapolis, IN" stay US.
      const decisive = !collidesWithState || (
        tail === code
        && (parts.length >= 3 || NON_US_LOCATION_RE.test(head))
      );
      if (decisive && !US_LOCATION_STRONG_RE.test(head)) return true;
    }
  }
  if (US_LOCATION_RE.test(loc)) return false;
  return NON_US_LOCATION_RE.test(loc);
}

function isIntelAgencyEmployer(company, url) {
  const co = (company || "").trim();
  if (co && INTEL_AGENCY_COMPANY_RE.test(co)) return true;
  const u = (url || "").trim();
  if (u && INTEL_AGENCY_URL_RE.test(u)) return true;
  return false;
}

function requiresSecurityClearance({ title, company, location, description, url } = {}) {
  if (isIntelAgencyEmployer(company, url)) return true;
  const blob = [title, company, location, description].map(x => x || "").join(" ");
  if (!blob.trim()) return false;
  const cleaned = blob.replace(CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE, " ");
  return CLEARANCE_REQUIREMENT_RE.test(cleaned);
}

function jobRequiresClearance(job) {
  return requiresSecurityClearance({
    title: job.title,
    company: job.company,
    location: job.location,
    description: job.job_description,
    url: job.apply_url || job.job_url || "",
  });
}

function isStaleListing(job) {
  const t = datePostedTime(job);
  if (t == null) return false; // unknown date - benefit of the doubt, don't hide it
  const ageDays = (Date.now() - t) / 86400000;
  return ageDays > STALE_LISTING_MAX_AGE_DAYS;
}

const MAX_ACCEPTABLE_MIN_YOE = 6;
// Optional adjective(s) between "years of" and "experience" — keep in sync
// with scripts/discovery_filters.py (_YOE_OF_EXP).
const YOE_OF_EXP = "(?:of\\s+)?(?:\\w+\\s+){0,3}(?:experience|exp\\.?|yoe)";
const YOE_PLUS = "(?:\\\\?\\+)";
const YOE_MIN_PLUS_RE = new RegExp(
  String.raw`\b(?:minimum(?:\s+of)?|min(?:imum)?\.?|at\s+least|requires?(?:\s+a)?|must\s+have|seeking|looking\s+for|with)\s+(\d{1,2})\s*${YOE_PLUS}\s*(?:years?|yrs?\.?)\s*(?:${YOE_OF_EXP})?\b`,
  "gi"
);
const YOE_YEARS_PLUS_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*${YOE_PLUS}\s*(?:years?|yrs?\.?)\s*${YOE_OF_EXP}\b`,
  "gi"
);
const YOE_YEARS_EXPERIENCE_RE = new RegExp(
  String.raw`\b(?:minimum(?:\s+of)?|min(?:imum)?\.?|at\s+least|requires?(?:\s+a)?|must\s+have|seeking|with)\s+(\d{1,2})\s*(?:years?|yrs?\.?)\s*${YOE_OF_EXP}\b`,
  "gi"
);
const YOE_PLAIN_YEARS_EXP_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*(?:years?|yrs?\.?)\s*${YOE_OF_EXP}\b`,
  "gi"
);
const YOE_RANGE_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*(?:\\?[-–—]|to)\s*(\d{1,2})\s*(?:\+)?\s*(?:years?|yrs?\.?)\s*(?:${YOE_OF_EXP})?\b`,
  "gi"
);
const YOE_LABEL_RE = /\b(?:yoe|years?\s+of\s+experience|years?\s+experience)\s*[:=]\s*(\d{1,2})\s*\+?/gi;
const YOE_TENURE_BEFORE_RE = /(?:(?:more|over)\s+than|(?:nearly|almost|approximately|around|about)|(?:for|with)\s+(?:over|more\s+than)|(?:founded|established|celebrating)|(?:company|holding|firm|business|organization|leader|provider)(?:\s+\w+){0,4}\s+with)\s*$/i;
// Tier-2 YOE display fallback — prefer recall; keep in sync with discovery_filters.py
const YOE_FB_WORD = String.raw`[\w/+&.,'-]+`;
const YOE_FB_EXP = "(?:experience|exper(?:ience)?|exp\\.?|yoe)";
const YOE_FB_CTX = "(?:experience|exper(?:ience)?|exp\\.?|yoe|engineering|software|development|industry|professional|relevant|work(?:ing)?|ml|ai|data)";
const YOE_FALLBACK_YEARS_OF_WORDS_EXP_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*${YOE_PLUS}?\s*(?:years?|yrs?\.?)\s+(?:of\s+)?(?:${YOE_FB_WORD}\s+){0,8}${YOE_FB_EXP}\b`,
  "gi"
);
const YOE_FALLBACK_YEARS_APOS_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*${YOE_PLUS}?\s*years?'\s*(?:of\s+)?${YOE_FB_EXP}\b`,
  "gi"
);
const YOE_FALLBACK_YEARS_NEAR_EXP_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*${YOE_PLUS}?\s*(?:years?|yrs?\.?)\b(?:(?!\b(?:years?|yrs?\.?)\b)[\s\S]){0,80}?\b${YOE_FB_EXP}\b`,
  "gi"
);
const YOE_FALLBACK_YEARS_PLUS_CTX_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*${YOE_PLUS}\s*(?:years?|yrs?\.?)\b(?:(?!\b(?:years?|yrs?\.?)\b)[\s\S]){0,60}?\b${YOE_FB_CTX}\b`,
  "gi"
);
const YOE_FALLBACK_AT_LEAST_RE = new RegExp(
  String.raw`\b(?:minimum(?:\s+of)?|min(?:imum)?\.?|at\s+least)\s+(\d{1,2})\s*${YOE_PLUS}?\s*(?:years?|yrs?\.?)\s+(?:of\s+)?(?:${YOE_FB_WORD}\s+){0,8}${YOE_FB_CTX}\b`,
  "gi"
);
const YOE_FALLBACK_YEARS_MINIMUM_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*${YOE_PLUS}?\s*(?:years?|yrs?\.?)\s+minimum\b(?!\s+age\b)(?:\s+(?:of\s+)?${YOE_FB_CTX})?`,
  "gi"
);
const YOE_FALLBACK_EXP_LABEL_RE = new RegExp(
  String.raw`\b${YOE_FB_EXP}\s*(?:required)?\s*[:=]\s*(\d{1,2})\s*${YOE_PLUS}?\s*(?:years?|yrs?\.?|yoe)?\b`,
  "gi"
);
const YOE_FALLBACK_YOE_ABBREV_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*${YOE_PLUS}?\s*yoe\b`,
  "gi"
);
const YOE_FALLBACK_RANGE_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*(?:\\?[-–—]|to)\s*(\d{1,2})\s*(?:\+)?\s*(?:years?|yrs?\.?)\s+(?:of\s+)?(?:${YOE_FB_WORD}\s+){0,8}${YOE_FB_EXP}\b`,
  "gi"
);
const YOE_FALLBACK_IN_ROLE_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*${YOE_PLUS}\s*(?:years?|yrs?\.?)\s+(?:in|as)\s+(?:an?\s+|the\s+)?(?:${YOE_FB_WORD}\s+){0,8}(?:role|position|capacity|job|engineer|scientist|analyst)\b`,
  "gi"
);
const YOE_FALLBACK_WORKING_AS_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*${YOE_PLUS}\s*(?:years?|yrs?\.?)\s+(?:working\s+)?(?:as|as\s+an?)\s+`,
  "gi"
);
const YOE_FALLBACK_YEARS_IN_FIELD_RE = new RegExp(
  String.raw`\b(\d{1,2})\s*${YOE_PLUS}\s*(?:years?|yrs?\.?)\s+in\s+(?:${YOE_FB_WORD}\s+){0,6}(?:engineering|science|analytics|development|software|data|ml|ai)\b`,
  "gi"
);
const CITIZENSHIP_OR_GC_REQUIREMENT_RE = /(\b(?:u\.?s\.?|us|united\s+states)\s+citizens?\s+only\b|\bonly\s+(?:u\.?s\.?|us|united\s+states)\s+citizens?\b|\b(?:u\.?s\.?|us|united\s+states)\s+citizenship\s+required\b|\bmust\s+be\s+(?:a\s+)?(?:u\.?s\.?|us|united\s+states)\s+citizen\b|\brequire[sd]?\s+(?:u\.?s\.?|us|united\s+states)\s+citizenship\b|\bcitizenship\s*(?:requirement|:)\s*(?:u\.?s\.?|us|united\s+states)\b|\bgreen\s*card\s+required\b|\bmust\s+(?:have|hold|possess)\s+(?:a\s+)?green\s*card\b|\brequire[sd]?\s+(?:a\s+)?green\s*card\b|\bmust\s+be\s+(?:a\s+)?(?:permanent\s+resident|lawful\s+permanent\s+resident)\b|\b(?:permanent\s+resident|lawful\s+permanent\s+resident)\s+(?:status\s+)?required\b|\bonly\s+(?:u\.?s\.?|us)\s+(?:citizens?|permanent\s+residents?)\b|\b(?:citizens?|permanent\s+residents?)\s+only\b)/i;
const WORK_MODE_HYBRID_RE = /(\bhybrid\b|\bremote\s+and\s+(?:in[\s\-]?office|on[\s\-]?site|onsite)\b|\b(?:in[\s\-]?office|on[\s\-]?site|onsite)\s+and\s+remote\b|\b\d+\s*(?:days?|x)\s+(?:a|per)\s+week\s+in\s+(?:the\s+)?(?:office|on[\s\-]?site)\b|\b(?:partially|part[\s\-]?time)\s+remote\b)/i;
const WORK_MODE_REMOTE_RE = /(\bfully\s+remote\b|\bremote[\s\-]?first\b|\bwork\s+from\s+home\b|\bwfh\b|\bremote\b)/i;
const WORK_MODE_ONSITE_RE = /(\bon[\s\-]?site\b|\bonsite\b|\bin[\s\-]?person\b|\bin[\s\-]?office\b|\bmust\s+relocate\b|\brelocation\s+required\b|\bon[\s\-]?campus\b)/i;
const WORK_MODE_FALLBACK_HYBRID_RE = /(\bhybrid[\s\-]?(?:preferred|ok|okay|available|possible|friendly|role|position)\b|\bopen\s+to\s+hybrid\b|\bflexible\s+(?:work\s+)?(?:arrangement|location|schedule)\b|\bflex(?:ible)?\s+work\b|\bmix\s+of\s+(?:remote|office|on[\s\-]?site|onsite|in[\s\-]?office)\b|\b\d+\s*(?:[-–—]\s*\d+\s+)?days?\s+(?:a|per)\s+week\s+(?:in|at|from)\s+(?:the\s+)?(?:office|hq|headquarters)\b)/i;
const WORK_MODE_FALLBACK_REMOTE_RE = /(\bopen\s+to\s+remote\b|\bremote[\s\-]?(?:ok|okay|friendly|preferred|available|possible|optional)\b|\boptional(?:ly)?\s+remote\b|\bwork\s+remotely\b|\bcan\s+be\s+remote\b|\bdistributed\s+team\b|\bwork\s+from\s+anywhere\b|\btelecommute\b|\btelework\b|\bremotely\b|\banywhere\s+in\s+(?:the\s+)?(?:u\.?s\.?|united\s+states)\b)/i;
const WORK_MODE_FALLBACK_ONSITE_RE = /(\boffice[\s\-]?based\b|\bheadquarters[\s\-]?based\b|\bhq[\s\-]?based\b|\bcome\s+into\s+(?:the\s+)?office\b|\bin\s+our\s+(?:offices?|hq|headquarters)\b|\bnot\s+remote\b|\bno\s+remote\b|\bon[\s\-]?site\s+only\b|\breport(?:ing)?\s+to\s+(?:the\s+)?(?:office|hq)\b|\boffice\s+presence\s+required\b)/i;

// Salary (UI only) — keep in sync with scripts/discovery_filters.py
const SAL_NUM_COMMA = String.raw`\d{1,3}(?:,\d{3})+`;
const SAL_NUM_K = String.raw`\d{2,3}(?:\.\d{1,2})?\s*[kK]`;
const SAL_NUM_PLAIN = String.raw`\d{5,7}`;
const SAL_NUM = `(?:${SAL_NUM_COMMA}|${SAL_NUM_K}|${SAL_NUM_PLAIN})`;
const SAL_CUR = String.raw`(?:\$|USD\s+)`;
const SAL_AMOUNT = `(?:${SAL_CUR}\\s*)?${SAL_NUM}`;
const SAL_SEP = String.raw`(?:\s*[-–—]\s*|\s+to\s+)`;
const SALARY_KW = String.raw`(?:salary|compensation|compensat(?:ed|ion)?|base(?:\s+pay|\s+salary)?|pay|ote|total\s+comp(?:ensation)?|\btc\b|remuneration|wages?)`;
const SALARY_RANGE_RE = new RegExp(`(${SAL_AMOUNT})${SAL_SEP}(${SAL_AMOUNT})`, "gi");
const SALARY_LABEL_RE = new RegExp(
  String.raw`\b${SALARY_KW}\s*(?:range|band|expectation)?\s*[:=]?\s*(${SAL_AMOUNT})(?:${SAL_SEP}(${SAL_AMOUNT}))?`,
  "gi"
);
const SALARY_DOLLAR_SINGLE_RE = new RegExp(`(?:${SAL_CUR}\\s*)(${SAL_NUM})\\b`, "gi");
const SALARY_FALLBACK_UP_TO_RE = new RegExp(
  String.raw`\b(?:up\s+to|as\s+high\s+as|capped\s+at|max(?:imum)?(?:\s+of)?)\s+(${SAL_AMOUNT})\b`,
  "gi"
);
const SALARY_FALLBACK_FROM_RE = new RegExp(
  String.raw`\b(?:starting\s+at|from|at\s+least|minimum(?:\s+of)?)\s+(${SAL_AMOUNT})\b`,
  "gi"
);
const SALARY_FALLBACK_NEAR_KW_RE = new RegExp(
  `(?:${SALARY_KW})[\\s\\S]{0,48}?(${SAL_AMOUNT})(?:${SAL_SEP}(${SAL_AMOUNT}))?|(${SAL_AMOUNT})(?:${SAL_SEP}(${SAL_AMOUNT}))?[\\s\\S]{0,48}?(?:${SALARY_KW})`,
  "gi"
);
const SALARY_FALLBACK_BARE_K_RANGE_RE = new RegExp(
  String.raw`\b(\d{2,3}(?:\.\d{1,2})?\s*[kK])${SAL_SEP}(\d{2,3}(?:\.\d{1,2})?\s*[kK])\b`,
  "gi"
);
const SALARY_HOURLY_AFTER_RE = /^\s*(?:\/|\s)*(?:hr|hrs|hour|hours|hourly)\b|^\s*per\s+hour\b|^\s*an\s+hour\b|^\s*p\/?h\b/i;
const SALARY_HOURLY_BEFORE_RE = /(?:hourly|per[\s\-]?hour|\/hr|\/hour)\s*$/i;
const SALARY_FUNDING_AMOUNT_RE = /(?:\$|USD\s*)?\s*\d+(?:\.\d+)?\s*[mMbB]\b|(?:\$|USD\s*)?\s*\d[\d,]*(?:\.\d+)?\s*(?:million|billion)\b/i;
const SALARY_FUNDING_CTX_RE = /\b(?:series\s+[a-z]|raised|funding\s+round|valuation|seed\s+round|venture|invest(?:ed|ment)|arr\b|revenue\s+of)\b/i;
const SALARY_MIN_ANNUAL = 20000;
const SALARY_MAX_ANNUAL = 1000000;

function parseSalaryAmount(raw) {
  let s = String(raw || "").trim();
  if (!s) return null;
  s = s.replace(/^(?:\$|USD)\s*/i, "").replace(/,/g, "").replace(/\s+/g, "");
  if (!s) return null;
  if (/[kK]$/.test(s)) {
    const n = parseFloat(s.slice(0, -1));
    return Number.isFinite(n) ? Math.round(n * 1000) : null;
  }
  if (/[mMbB]$/.test(s)) return null;
  const n = parseFloat(s);
  return Number.isFinite(n) ? Math.round(n) : null;
}

function salarySane(n) {
  return n != null && n >= SALARY_MIN_ANNUAL && n <= SALARY_MAX_ANNUAL;
}

function salaryIsHourly(blob, start, end) {
  const pre = blob.slice(Math.max(0, start - 24), start);
  const post = blob.slice(end, Math.min(blob.length, end + 24));
  return SALARY_HOURLY_BEFORE_RE.test(pre) || SALARY_HOURLY_AFTER_RE.test(post);
}

function salaryIsFundingNoise(blob, start, end) {
  const window = blob.slice(Math.max(0, start - 40), Math.min(blob.length, end + 40));
  return SALARY_FUNDING_AMOUNT_RE.test(window) || SALARY_FUNDING_CTX_RE.test(window);
}

function salaryPairFromAmounts(aRaw, bRaw) {
  const a = aRaw ? parseSalaryAmount(aRaw) : null;
  const b = bRaw ? parseSalaryAmount(bRaw) : null;
  if (!salarySane(a) && !salarySane(b)) return null;
  if (salarySane(a) && salarySane(b)) {
    return { min: Math.min(a, b), max: Math.max(a, b), period: "year" };
  }
  return { min: salarySane(a) ? a : b, max: null, period: "year" };
}

function salaryBlob(text, title, description) {
  return [text, title, description].filter(Boolean).map(x => String(x || "")).join(" ");
}

function extractSalary(text, title, description) {
  const blob = salaryBlob(text, title, description);
  if (!blob.trim()) return null;
  const candidates = [];
  const rangeSpans = [];
  let m;
  SALARY_LABEL_RE.lastIndex = 0;
  while ((m = SALARY_LABEL_RE.exec(blob)) !== null) {
    if (salaryIsHourly(blob, m.index, m.index + m[0].length)) continue;
    if (salaryIsFundingNoise(blob, m.index, m.index + m[0].length)) continue;
    const pair = salaryPairFromAmounts(m[1], m[2]);
    if (pair) {
      candidates.push(pair);
      if (m[2]) rangeSpans.push([m.index, m.index + m[0].length]);
    }
  }
  SALARY_RANGE_RE.lastIndex = 0;
  while ((m = SALARY_RANGE_RE.exec(blob)) !== null) {
    const aRaw = m[1];
    const bRaw = m[2];
    const hasCur = /(?:\$|USD)/i.test(aRaw) || /(?:\$|USD)/i.test(bRaw);
    const bothKOrPlain = /(?:[kK]|\d{5,7})/.test(aRaw) && /(?:[kK]|\d{5,7})/.test(bRaw);
    if (!(hasCur || bothKOrPlain)) continue;
    if (salaryIsHourly(blob, m.index, m.index + m[0].length)) continue;
    if (salaryIsFundingNoise(blob, m.index, m.index + m[0].length)) continue;
    const pair = salaryPairFromAmounts(aRaw, bRaw);
    if (pair) {
      candidates.push(pair);
      rangeSpans.push([m.index, m.index + m[0].length]);
    }
  }
  SALARY_DOLLAR_SINGLE_RE.lastIndex = 0;
  while ((m = SALARY_DOLLAR_SINGLE_RE.exec(blob)) !== null) {
    if (rangeSpans.some(([rs, re]) => rs <= m.index && m.index < re)) continue;
    if (salaryIsHourly(blob, m.index, m.index + m[0].length)) continue;
    if (salaryIsFundingNoise(blob, m.index, m.index + m[0].length)) continue;
    const pair = salaryPairFromAmounts(m[1], null);
    if (pair) candidates.push(pair);
  }
  if (!candidates.length) return null;
  const ranged = candidates.filter(c => c.max != null);
  return ranged.length ? ranged[0] : candidates[0];
}

function extractSalaryFallback(text, title, description) {
  if (extractSalary(text, title, description) != null) return null;
  const blob = salaryBlob(text, title, description);
  if (!blob.trim()) return null;
  const candidates = [];
  let m;
  SALARY_FALLBACK_NEAR_KW_RE.lastIndex = 0;
  while ((m = SALARY_FALLBACK_NEAR_KW_RE.exec(blob)) !== null) {
    if (salaryIsHourly(blob, m.index, m.index + m[0].length)) continue;
    if (salaryIsFundingNoise(blob, m.index, m.index + m[0].length)) continue;
    const pair = salaryPairFromAmounts(m[1] || m[3], m[2] || m[4]);
    if (pair) candidates.push(pair);
  }
  SALARY_FALLBACK_UP_TO_RE.lastIndex = 0;
  while ((m = SALARY_FALLBACK_UP_TO_RE.exec(blob)) !== null) {
    if (salaryIsHourly(blob, m.index, m.index + m[0].length)) continue;
    if (salaryIsFundingNoise(blob, m.index, m.index + m[0].length)) continue;
    const pair = salaryPairFromAmounts(m[1], null);
    if (pair) candidates.push(pair);
  }
  SALARY_FALLBACK_FROM_RE.lastIndex = 0;
  while ((m = SALARY_FALLBACK_FROM_RE.exec(blob)) !== null) {
    if (salaryIsHourly(blob, m.index, m.index + m[0].length)) continue;
    if (salaryIsFundingNoise(blob, m.index, m.index + m[0].length)) continue;
    const pair = salaryPairFromAmounts(m[1], null);
    if (pair) candidates.push(pair);
  }
  SALARY_FALLBACK_BARE_K_RANGE_RE.lastIndex = 0;
  while ((m = SALARY_FALLBACK_BARE_K_RANGE_RE.exec(blob)) !== null) {
    if (salaryIsHourly(blob, m.index, m.index + m[0].length)) continue;
    if (salaryIsFundingNoise(blob, m.index, m.index + m[0].length)) continue;
    const pair = salaryPairFromAmounts(m[1], m[2]);
    if (pair) candidates.push(pair);
  }
  if (!candidates.length) return null;
  const ranged = candidates.filter(c => c.max != null);
  return ranged.length ? ranged[0] : candidates[0];
}

function extractMinRequiredYoe(text, title, description) {
  let blob = [text, title, description].filter(Boolean).map(x => String(x || "")).join(" ");
  if (!blob.trim()) return null;
  blob = blob.replace(/\\\+/g, "+").replace(/\\-/g, "-");
  const mins = [];
  const rangeSpans = [];
  const isTenure = (start) => YOE_TENURE_BEFORE_RE.test(blob.slice(Math.max(0, start - 64), start));
  YOE_RANGE_RE.lastIndex = 0;
  let m;
  while ((m = YOE_RANGE_RE.exec(blob)) !== null) {
    if (isTenure(m.index)) continue;
    const lo = parseInt(m[1], 10);
    const hi = parseInt(m[2], 10);
    mins.push(Math.min(lo, hi));
    rangeSpans.push([m.index, m.index + m[0].length]);
  }
  const inRangeSpan = (start) => rangeSpans.some(([rs, re]) => rs <= start && start < re);
  for (const rx of [YOE_MIN_PLUS_RE, YOE_YEARS_PLUS_RE, YOE_YEARS_EXPERIENCE_RE, YOE_LABEL_RE, YOE_PLAIN_YEARS_EXP_RE]) {
    rx.lastIndex = 0;
    while ((m = rx.exec(blob)) !== null) {
      if (inRangeSpan(m.index)) continue;
      if (isTenure(m.index)) continue;
      mins.push(parseInt(m[1], 10));
    }
  }
  if (!mins.length) return null;
  const sane = mins.filter(n => n > 0 && n <= 40);
  return sane.length ? Math.max(...sane) : null;
}

function extractMinRequiredYoeFallback(text, title, description) {
  if (extractMinRequiredYoe(text, title, description) != null) return null;
  let blob = [text, title, description].filter(Boolean).map(x => String(x || "")).join(" ");
  if (!blob.trim()) return null;
  blob = blob.replace(/\\\+/g, "+").replace(/\\-/g, "-");
  const isTenure = (start) => YOE_TENURE_BEFORE_RE.test(blob.slice(Math.max(0, start - 64), start));
  const mins = [];
  const rangeSpans = [];
  let m;
  YOE_FALLBACK_RANGE_RE.lastIndex = 0;
  while ((m = YOE_FALLBACK_RANGE_RE.exec(blob)) !== null) {
    if (isTenure(m.index)) continue;
    const lo = parseInt(m[1], 10);
    const hi = parseInt(m[2], 10);
    mins.push(Math.min(lo, hi));
    rangeSpans.push([m.index, m.index + m[0].length]);
  }
  const inRangeSpan = (start) => rangeSpans.some(([rs, re]) => rs <= start && start < re);
  for (const rx of [
    YOE_FALLBACK_YEARS_OF_WORDS_EXP_RE,
    YOE_FALLBACK_YEARS_APOS_RE,
    YOE_FALLBACK_YEARS_NEAR_EXP_RE,
    YOE_FALLBACK_YEARS_PLUS_CTX_RE,
    YOE_FALLBACK_AT_LEAST_RE,
    YOE_FALLBACK_YEARS_MINIMUM_RE,
    YOE_FALLBACK_EXP_LABEL_RE,
    YOE_FALLBACK_YOE_ABBREV_RE,
    YOE_FALLBACK_IN_ROLE_RE,
    YOE_FALLBACK_WORKING_AS_RE,
    YOE_FALLBACK_YEARS_IN_FIELD_RE,
  ]) {
    rx.lastIndex = 0;
    while ((m = rx.exec(blob)) !== null) {
      if (inRangeSpan(m.index)) continue;
      if (isTenure(m.index)) continue;
      mins.push(parseInt(m[1], 10));
    }
  }
  if (!mins.length) return null;
  const sane = mins.filter(n => n > 0 && n <= 40);
  return sane.length ? Math.max(...sane) : null;
}

function requiresExcessiveExperience({ title, description, text } = {}) {
  const ymin = extractMinRequiredYoe(text, title, description);
  return ymin != null && ymin > MAX_ACCEPTABLE_MIN_YOE;
}

function requiresUsCitizenOrGreencard({ title, description, text } = {}) {
  const blob = [text, title, description].filter(Boolean).map(x => String(x || "")).join(" ");
  if (!blob.trim()) return false;
  return CITIZENSHIP_OR_GC_REQUIREMENT_RE.test(blob);
}

function detectWorkMode({ title, location, description } = {}) {
  const blob = [title, location, description].map(x => x || "").join(" ");
  if (!blob.trim()) return "unknown";
  if (WORK_MODE_HYBRID_RE.test(blob)) return "hybrid";
  const remote = WORK_MODE_REMOTE_RE.test(blob);
  const onsite = WORK_MODE_ONSITE_RE.test(blob);
  if (remote && onsite) return "unknown";
  if (remote) return "remote";
  if (onsite) return "onsite";
  return "unknown";
}

function detectWorkModeFallback({ title, location, description } = {}) {
  if (detectWorkMode({ title, location, description }) !== "unknown") return "unknown";
  const blob = [title, location, description].map(x => x || "").join(" ");
  if (!blob.trim()) return "unknown";
  if (WORK_MODE_FALLBACK_HYBRID_RE.test(blob)) return "hybrid";
  const remote = WORK_MODE_FALLBACK_REMOTE_RE.test(blob);
  const onsite = WORK_MODE_FALLBACK_ONSITE_RE.test(blob);
  if (remote && onsite) return "unknown";
  if (remote) return "remote";
  if (onsite) return "onsite";
  return "unknown";
}

/** Prefer expanded full JD from /description when cached; else preview. */
function jobDescriptionText(job) {
  if (!job) return "";
  const cached = typeof jdCache !== "undefined" ? jdCache.get(job.id) : null;
  if (cached && cached.text) return cached.text;
  return job.job_description || "";
}

function jobMinYoe(job) {
  if (job && job.min_yoe != null && job.min_yoe !== "") {
    const n = Number(job.min_yoe);
    if (!Number.isNaN(n)) return n;
  }
  return extractMinRequiredYoe(null, job && job.title, jobDescriptionText(job));
}

function jobMinYoeDisplay(job) {
  const strict = jobMinYoe(job);
  if (strict != null) return { n: strict, approx: false };
  if (job && job.min_yoe_fallback != null && job.min_yoe_fallback !== "") {
    const n = Number(job.min_yoe_fallback);
    if (!Number.isNaN(n)) return { n, approx: true };
  }
  const fb = extractMinRequiredYoeFallback(null, job && job.title, jobDescriptionText(job));
  if (fb != null) return { n: fb, approx: true };
  return { n: null, approx: false };
}

/** Display work mode: strict first, else fallback. approx=true → prefix ~. */
function jobWorkModeDisplay(job) {
  const raw = job && job.work_mode;
  if (raw === "remote" || raw === "hybrid" || raw === "onsite") {
    return { mode: raw, approx: false };
  }
  const strict = detectWorkMode({
    title: job && job.title,
    location: job && job.location,
    description: jobDescriptionText(job),
  });
  if (strict !== "unknown") return { mode: strict, approx: false };
  const stampedFb = job && job.work_mode_fallback;
  if (stampedFb === "remote" || stampedFb === "hybrid" || stampedFb === "onsite") {
    return { mode: stampedFb, approx: true };
  }
  const fb = detectWorkModeFallback({
    title: job && job.title,
    location: job && job.location,
    description: jobDescriptionText(job),
  });
  if (fb !== "unknown") return { mode: fb, approx: true };
  return { mode: "unknown", approx: false };
}

function jobWorkMode(job) {
  return jobWorkModeDisplay(job).mode;
}

function jobSalaryDisplay(job) {
  const stampedMin = job && job.salary_min;
  if (stampedMin != null && stampedMin !== "") {
    const n = Number(stampedMin);
    if (!Number.isNaN(n)) {
      const hi = job.salary_max != null && job.salary_max !== ""
        ? Number(job.salary_max) : null;
      return {
        min: n,
        max: hi != null && !Number.isNaN(hi) ? hi : null,
        approx: false,
      };
    }
  }
  const live = extractSalary(null, job && job.title, jobDescriptionText(job));
  if (live) return { min: live.min, max: live.max ?? null, approx: false };
  const fbMin = job && job.salary_min_fallback;
  if (fbMin != null && fbMin !== "") {
    const n = Number(fbMin);
    if (!Number.isNaN(n)) {
      const hi = job.salary_max_fallback != null && job.salary_max_fallback !== ""
        ? Number(job.salary_max_fallback) : null;
      return {
        min: n,
        max: hi != null && !Number.isNaN(hi) ? hi : null,
        approx: true,
      };
    }
  }
  const fb = extractSalaryFallback(null, job && job.title, jobDescriptionText(job));
  if (fb) return { min: fb.min, max: fb.max ?? null, approx: true };
  return { min: null, max: null, approx: false };
}

function jobRequiresExcessiveYoe(job) {
  const ymin = jobMinYoe(job);
  return ymin != null && ymin > MAX_ACCEPTABLE_MIN_YOE;
}

function jobRequiresCitizenOrGc(job) {
  return requiresUsCitizenOrGreencard({
    title: job.title,
    description: job.job_description,
  });
}

function formatYoeLabel(n, approx = false, compact = false) {
  if (n == null || n === "") return "";
  const num = Number(n);
  if (Number.isNaN(num)) return "";
  return `${approx ? "~" : ""}${num}+${compact ? "" : " years"}`;
}

function formatCompactSalaryK(n) {
  const num = Number(n);
  if (!Number.isFinite(num)) return "";
  const k = num / 1000;
  const nearest = Math.round(k);
  if (Math.abs(k - nearest) < 0.05) return `${nearest}K`;
  const oneDec = Math.round(k * 10) / 10;
  if (Math.abs(oneDec - Math.round(oneDec)) < 0.05) return `${Math.round(oneDec)}K`;
  return `${oneDec}K`;
}

function formatSalaryLabel(min, max, { approx = false, compact = true } = {}) {
  const lo = min != null && min !== "" ? Number(min) : null;
  const hi = max != null && max !== "" ? Number(max) : null;
  const loOk = lo != null && !Number.isNaN(lo);
  const hiOk = hi != null && !Number.isNaN(hi);
  if (!loOk && !hiOk) return "";
  const fmt = (n) => {
    if (compact) return `$${formatCompactSalaryK(n)}`;
    return `$${Math.round(n).toLocaleString("en-US")}`;
  };
  let body;
  if (loOk && hiOk && lo !== hi) body = `${fmt(lo)}–${fmt(hi)}`;
  else body = fmt(loOk ? lo : hi);
  return `${approx ? "~" : ""}${body}`;
}

function formatWorkMode(mode, approx = false) {
  const m = String(mode || "").toLowerCase();
  let label = "";
  if (m === "remote") label = "Remote";
  else if (m === "hybrid") label = "Hybrid";
  else if (m === "onsite" || m === "on-site") label = "In person";
  else return "—";
  return `${approx ? "~" : ""}${label}`;
}

function isHiddenUntouchedListing(job) {
  // Only ever applies to a job that's still exactly where discovery left
  // it - never to anything already started, stuck, reviewed, or applied.
  // Excessive YOE / citizen-GC are a client safety net; server backfill
  // also moves them to deleted.
  return job.status === "discovered" && (
    isExcludedTitle(job.title)
    || isStaleListing(job)
    || isClearlyNonUsLocation(job.location)
    || jobRequiresClearance(job)
    || jobRequiresExcessiveYoe(job)
    || jobRequiresCitizenOrGc(job)
  );
}

function statusPriorityIndex(status) {
  const idx = PRIORITY_ORDER.indexOf(status);
  return idx === -1 ? PRIORITY_ORDER.length : idx;
}

let jobs = [];
let selectedId = null;
let activityEvents = [];
let expandedCompanies = new Set();
/** Lazy-loaded cleaned JDs: jobId -> { loading, text, error, source } */
const jdCache = new Map();
let statusFilterMode = "open"; // "open" | "stuck" | "ready" | "applied" | "deleted"
let appliedSortKey = "date"; // company | title | location | source | date
let appliedSortDir = "desc"; // asc | desc
let editingAppliedId = null;
let scrollToAppliedDetail = false;
/** Server: headed fill/CAPTCHA/Ready hold still live (UI-008). */
let fillHoldActive = false;

const TEST_MODE_STORAGE_KEY = "jobHunterTestMode";
const PARTYROCK_STORAGE_KEY = "jobHunterPartyRock";
// Keep in sync with dashboard/server.py DISCOVERY_SOURCE_DEFS.
const DISCOVERY_SOURCE_CATALOG = [
  { id: "indeed", label: "Indeed" },
  { id: "linkedin", label: "LinkedIn" },
  { id: "greenhouse", label: "Greenhouse" },
  { id: "lever", label: "Lever" },
  { id: "ashby", label: "Ashby" },
  { id: "recruitee", label: "Recruitee" },
  { id: "personio", label: "Personio" },
  { id: "smartrecruiters", label: "SmartRecruiters" },
  { id: "workable", label: "Workable" },
  { id: "rippling", label: "Rippling" },
  { id: "breezy", label: "Breezy" },
  { id: "bamboohr", label: "BambooHR" },
  { id: "builtin", label: "Built In" },
];
const DISCOVERY_SOURCES_STORAGE_KEY = "jobHunterDiscoverySources";

function loadTestModeSetting() {
  const raw = localStorage.getItem(TEST_MODE_STORAGE_KEY);
  if (raw === null) return true;
  return raw !== "0" && raw !== "false";
}

function saveTestModeSetting(on) {
  localStorage.setItem(TEST_MODE_STORAGE_KEY, on ? "1" : "0");
}

/** Default ON — PartyRock tailor before Test Mode Start fill. */
function loadPartyRockSetting() {
  const raw = localStorage.getItem(PARTYROCK_STORAGE_KEY);
  if (raw === null) return true;
  return raw !== "0" && raw !== "false";
}

function savePartyRockSetting(on) {
  localStorage.setItem(PARTYROCK_STORAGE_KEY, on ? "1" : "0");
}

function defaultDiscoverySourceMap() {
  const m = {};
  for (const s of DISCOVERY_SOURCE_CATALOG) m[s.id] = true;
  return m;
}

function loadDiscoverySourceSettings() {
  const defaults = defaultDiscoverySourceMap();
  try {
    const raw = localStorage.getItem(DISCOVERY_SOURCES_STORAGE_KEY);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return defaults;
    for (const s of DISCOVERY_SOURCE_CATALOG) {
      if (Object.prototype.hasOwnProperty.call(parsed, s.id)) {
        defaults[s.id] = !!parsed[s.id];
      }
    }
    return defaults;
  } catch (e) {
    return defaults;
  }
}

function saveDiscoverySourceSettings(map) {
  const out = defaultDiscoverySourceMap();
  if (map && typeof map === "object") {
    for (const s of DISCOVERY_SOURCE_CATALOG) {
      if (Object.prototype.hasOwnProperty.call(map, s.id)) out[s.id] = !!map[s.id];
    }
  }
  localStorage.setItem(DISCOVERY_SOURCES_STORAGE_KEY, JSON.stringify(out));
  return out;
}

function enabledDiscoverySourceIds() {
  const map = loadDiscoverySourceSettings();
  return DISCOVERY_SOURCE_CATALOG.map(s => s.id).filter(id => map[id] !== false);
}

function toggleDiscoverySource(sourceId, checked) {
  const map = loadDiscoverySourceSettings();
  map[sourceId] = !!checked;
  if (!Object.values(map).some(Boolean)) {
    map[sourceId] = true;
    alert("Enable at least one discovery source.");
  }
  saveDiscoverySourceSettings(map);
  renderDiscoverPopover(discoveryState);
}

let testModeEnabled = loadTestModeSetting();
let partyRockEnabled = loadPartyRockSetting();

function statusLabel(s) {
  return (s || "unknown").replaceAll("_", " ");
}

function normalizeDeletedReasonCode(code) {
  const key = String(code || "").trim().toLowerCase();
  if (!key) return "";
  if (key === "clearance") return "clearance_or_intel";
  if (key === "seniority") return "management_track";
  if (key === "non_us") return "non_us_location";
  if (key === "manual") return "user";
  return key;
}

function deletedReasonCodes(job) {
  const raw = job?.deleted_reason ?? job?.deleted_reasons;
  if (raw == null || raw === "") return [];
  const parts = Array.isArray(raw)
    ? raw.map(c => String(c).trim()).filter(Boolean)
    : String(raw).split(/[,|;]+/).map(s => s.trim()).filter(Boolean);
  const seen = new Set();
  const out = [];
  for (const p of parts) {
    const n = normalizeDeletedReasonCode(p);
    if (!n || seen.has(n)) continue;
    seen.add(n);
    out.push(n);
  }
  return out;
}

function deletedReasonLabel(code, { short = false } = {}) {
  if (!code) return short ? "No reason" : "";
  const key = normalizeDeletedReasonCode(code);
  const entry = DELETED_REASON_LABELS[key] || DELETED_REASON_LABELS[String(code).trim().toLowerCase()];
  if (entry) return short ? entry.short : entry.long;
  const human = String(code).trim().replaceAll("_", " ");
  if (!human) return short ? "No reason" : "";
  return short ? human.replace(/\b\w/g, c => c.toUpperCase()) : human;
}

function formatDeletedReasons(job, { short = false } = {}) {
  const codes = deletedReasonCodes(job);
  if (!codes.length) return null;
  return codes.map(c => deletedReasonLabel(c, { short })).join(" · ");
}

function deletedReasonGroupKey(job) {
  const codes = deletedReasonCodes(job);
  return codes.length ? codes[0] : "";
}

function deletedReasonGroupLabel(key) {
  return deletedReasonLabel(key, { short: true }) || "No reason";
}

function deletedReasonGroupSortIndex(key) {
  const idx = DELETED_REASON_ORDER.indexOf(key);
  if (idx !== -1) return idx;
  return key ? DELETED_REASON_ORDER.length - 1.5 : DELETED_REASON_ORDER.length;
}

function renderStats() {
  const counts = {};
  for (const j of jobs) counts[j.status] = (counts[j.status] || 0) + 1;
  const open = jobs.filter(j => !TERMINAL.includes(j.status) && j.status !== "deleted" && !isHiddenUntouchedListing(j)).length;
  const stuckCount = (counts.stuck || 0) + (counts.blocked_captcha || 0);
  const deletedCount = counts.deleted || 0;
  const appliedCount = TEMP_APPLIED_COUNT_OVERRIDE != null
    ? TEMP_APPLIED_COUNT_OVERRIDE
    : (counts.applied || 0);
  const items = [
    ["Open", open, "open"],
    ["Stuck", stuckCount, "stuck"],
    ["Ready", counts.ready_for_review || 0, "ready"],
    ["Applied", appliedCount, "applied"],
    ["Deleted", deletedCount, "deleted"],
  ];
  document.getElementById("stats").innerHTML = items.map(([l, n, mode]) => {
    const title = mode === "applied"
      ? "Show applied jobs in the list and tracking table in the detail pane"
      : mode === "deleted"
        ? "Show soft-deleted jobs grouped by reason"
        : "Click to show just these jobs";
    const nColor = (l === "Stuck" || l === "Deleted") && n > 0
      ? (l === "Deleted" ? "var(--danger, #e05555)" : "var(--amber)")
      : "var(--text)";
    return `
    <div class="stat ${statusFilterMode === mode ? "stat-active" : ""}" onclick="setStatusFilter('${mode}')" title="${title}">
      <div class="n" style="color:${nColor}">${n}</div><div class="l">${l}</div>
    </div>
  `;
  }).join("");
}

function setStatusFilter(mode) {
  statusFilterMode = mode;
  // Drop selection when it no longer matches the active status filter so
  // the detail pane can show the Applied table (or the usual empty state).
  if (selectedId) {
    const job = jobs.find(j => j.id === selectedId);
    if (!job || !jobMatchesFilter(job)) selectedId = null;
  }
  render();
}

function jobMatchesFilter(j) {
  switch (statusFilterMode) {
    case "stuck": return j.status === "stuck" || j.status === "blocked_captcha";
    case "ready": return j.status === "ready_for_review";
    case "applied": return j.status === "applied";
    case "deleted": return j.status === "deleted";
    case "open":
    default: return !TERMINAL.includes(j.status) && j.status !== "deleted";
  }
}

function groupPriorityStatus(items) {
  let best = null, bestIdx = Infinity;
  for (const j of items) {
    const idx = PRIORITY_ORDER.indexOf(j.status);
    const effIdx = idx === -1 ? PRIORITY_ORDER.length : idx;
    if (effIdx < bestIdx) { bestIdx = effIdx; best = j.status; }
  }
  return best;
}

// datePostedSortKey / compareByPosted are provided by job_sort.js, shared with
// app.js so both UIs order Posted the same way.

function jsStringEscape(s) {
  return String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

function escapeAttr(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}

/** Posted-date chip, prefixed "~" when the date is only approximate.
 *  Reads jobPostedDisplay so the chip can't disagree with the Posted sort. */
function postedMetaLabel(job) {
  const { iso, approx } = jobPostedDisplay(job);
  if (!iso) return "";
  const formatted = formatDate(iso);
  return formatted ? (approx ? "~" : "") + formatted : "";
}

function renderJobRow(job, { nested = false, showCompany = true } = {}) {
  const color = STATUS_COLORS[job.status] || "#7a828c";
  const srcNames = jobSourceNames(job);
  const meta = [...srcNames, job.location, postedMetaLabel(job)].filter(Boolean);
  if (job.multi_opening) meta.push("multi openings");
  const { mode, approx: modeApprox } = jobWorkModeDisplay(job);
  let modeLabel = null;
  if (mode && mode !== "unknown") {
    modeLabel = formatWorkMode(mode, modeApprox);
    if (modeLabel) meta.push(modeLabel);
  }
  const { n: yminTag, approx: yoeApprox } = jobMinYoeDisplay(job);
  let yoeLabel = null;
  if (yminTag != null && !Number.isNaN(Number(yminTag))) {
    yoeLabel = formatYoeLabel(yminTag, yoeApprox, true);
    if (yoeLabel) meta.push(yoeLabel);
  }
  const { min: salMin, max: salMax, approx: salApprox } = jobSalaryDisplay(job);
  let salLabel = null;
  if (salMin != null || salMax != null) {
    salLabel = formatSalaryLabel(salMin, salMax, { approx: salApprox, compact: true });
    if (salLabel) meta.push(salLabel);
  }
  const delReason = job.status === "deleted" ? formatDeletedReasons(job, { short: true }) : null;
  if (delReason) meta.push(delReason);
  const srcSet = new Set(srcNames);
  return `
    <div class="row ${nested ? "nested" : ""} ${job.id === selectedId ? "active" : ""}" data-id="${escapeAttr(job.id)}" onclick="${escapeAttr(`event.stopPropagation(); selectJob('${jsStringEscape(job.id)}')`)}">
      ${showCompany ? `<div class="company">${escapeHtml(job.company) || "(fetching details…)"}</div>` : ""}
      <div class="title">${escapeHtml(job.title) || ""}</div>
      ${meta.length ? `<div class="meta">${meta.map(m => {
        let cls = "";
        if (m === "multi openings") cls = ' class="multi-opening-tag"';
        else if (delReason && m === delReason) cls = ' class="deleted-reason-tag"';
        else if (modeLabel && m === modeLabel) cls = ` class="work-mode-tag mode-tag ${escapeHtml(mode)}"`;
        else if (yoeLabel && m === yoeLabel) cls = ' class="yoe-tag"';
        else if (salLabel && m === salLabel) cls = ' class="salary-tag"';
        else if (srcSet.has(m)) cls = ' class="source-chip"';
        return `<span${cls}>${escapeHtml(m)}</span>`;
      }).join("")}</div>` : ""}
      <span class="badge" style="background:${color}22;color:${color}">${escapeHtml(
        job.status === "deleted" && delReason
          ? `Deleted · ${delReason}`
          : statusLabel(job.status)
      )}</span>
    </div>
  `;
}

function renderDeletedReasonGroups(items, sortBy) {
  const groups = new Map();
  for (const j of items) {
    const key = deletedReasonGroupKey(j);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(j);
  }
  const entries = Array.from(groups.entries()).map(([key, groupItems]) => {
    sortItems(groupItems, sortBy);
    return { key, items: groupItems, label: deletedReasonGroupLabel(key) };
  });
  entries.sort((a, b) => {
    const ai = deletedReasonGroupSortIndex(a.key);
    const bi = deletedReasonGroupSortIndex(b.key);
    if (ai !== bi) return ai - bi;
    return a.label.localeCompare(b.label);
  });
  return entries.map(({ items: groupItems, label }) => `
    <div class="row reason-header">
      <div class="company">${escapeHtml(label)} <span class="count">${groupItems.length}</span></div>
    </div>
    ${groupItems.map(job => renderJobRow(job, { nested: true })).join("")}
  `).join("");
}

function toggleCompany(company) {
  if (expandedCompanies.has(company)) expandedCompanies.delete(company);
  else expandedCompanies.add(company);
  render();
}

/** Group key for the current group-by mode (null when ungrouped). */
function jobGroupKey(job, groupBy) {
  if (!job || groupBy === "none") return null;
  if (groupBy === "source") return job.source || "(unknown source)";
  return job.company || "(unknown)";
}

/**
 * Single source of truth for list tint classes after any render/expand/select.
 * Job row: active iff id === selectedId.
 * Group header: active (darker orange) iff expanded OR contains selectedId.
 */
function syncListSelection() {
  const list = document.getElementById("list");
  if (!list) return;
  const groupBy = document.getElementById("group-by")?.value || "none";
  list.querySelectorAll(".row[data-id]").forEach(row => {
    row.classList.toggle("active", row.getAttribute("data-id") === selectedId);
  });
  const selectedKey = selectedId ? jobGroupKey(jobs.find(j => j.id === selectedId), groupBy) : null;
  list.querySelectorAll(".row.group-header[data-group]").forEach(header => {
    const key = header.getAttribute("data-group");
    header.classList.toggle("active", expandedCompanies.has(key) || (selectedKey != null && key === selectedKey));
  });
}

function populateSourceFilter() {
  const sel = document.getElementById("source-filter");
  if (!sel) return;
  const current = sel.value;
  const sources = Array.from(new Set(
    jobs.flatMap(j => jobSourceNames(j)).filter(Boolean)
  )).sort();
  sel.innerHTML = '<option value="">All sources</option>' +
    sources.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
  if (sources.includes(current)) sel.value = current;
}

function sortItems(items, sortBy) {
  if (sortBy === "company") items.sort((a, b) => (a.company || "").localeCompare(b.company || ""));
  else if (sortBy === "status") items.sort((a, b) => statusPriorityIndex(a.status) - statusPriorityIndex(b.status));
  else if (sortBy === "salary" || sortBy === "salary_asc") {
    const asc = sortBy === "salary_asc";
    items.sort((a, b) => {
      const am = jobSalaryDisplay(a).min;
      const bm = jobSalaryDisplay(b).min;
      const aUnk = am == null;
      const bUnk = bm == null;
      if (aUnk !== bUnk) return aUnk ? 1 : -1;
      if (am !== bm) return asc ? am - bm : bm - am;
      return compareByPosted(a, b);
    });
  }
  else if (sortBy === "multi_opening") {
    items.sort((a, b) => {
      const aM = !!a.multi_opening;
      const bM = !!b.multi_opening;
      if (aM !== bM) return aM ? -1 : 1;
      return compareByPosted(a, b);
    });
  }
  else items.sort(compareByPosted);
  return items;
}

function render() {
  renderStats();
  populateSourceFilter();
  window.__classicUpdateFiltersChrome?.();
  const list = document.getElementById("list");
  const filterText = (document.getElementById("filter-input")?.value || "").trim().toLowerCase();
  const sourceFilter = document.getElementById("source-filter")?.value || "";
  const groupBy = document.getElementById("group-by")?.value || "none";
  const sortBy = document.getElementById("sort-by")?.value || "date";

  let openJobs = jobs.filter(jobMatchesFilter).filter(j => !isHiddenUntouchedListing(j));
  if (filterText) {
    openJobs = openJobs.filter(j =>
      (j.company || "").toLowerCase().includes(filterText) ||
      (j.title || "").toLowerCase().includes(filterText)
    );
  }
  if (sourceFilter) {
    openJobs = openJobs.filter(j => {
      const names = jobSourceNames(j).map(n => n.toLowerCase());
      return names.includes(sourceFilter.toLowerCase()) || (j.source || "") === sourceFilter;
    });
  }

  const emptyHtml = `<div class="empty">${
    filterText || sourceFilter
      ? 'No matches — clear filters'
      : (jobs.length ? "No jobs match this filter." : "No jobs yet. Run discovery to populate the queue.")
  }</div>`;
  document.getElementById("list-pane")?.classList.toggle("deleted-theme", statusFilterMode === "deleted");

  if (statusFilterMode === "deleted") {
    list.innerHTML = openJobs.length
      ? renderDeletedReasonGroups(openJobs, sortBy)
      : emptyHtml;
    syncListSelection();
    renderDetail();
    return;
  }

  if (groupBy === "none") {
    sortItems(openJobs, sortBy);
    // In-progress/needs-attention jobs always float to the top, regardless
    // of sort mode - that's what you actually want to see first. Array.sort
    // is stable, so this preserves the sortItems() order within each tier.
    openJobs.sort((a, b) => {
      const aIP = IN_PROGRESS_OR_NEEDS_ATTENTION.includes(a.status);
      const bIP = IN_PROGRESS_OR_NEEDS_ATTENTION.includes(b.status);
      return aIP === bIP ? 0 : (aIP ? -1 : 1);
    });
    list.innerHTML = openJobs.map(job => renderJobRow(job, {})).join("") || emptyHtml;
    syncListSelection();
    renderDetail();
    return;
  }

  const groupKeyFn = groupBy === "source" ? (j => j.source || "(unknown source)") : (j => j.company || "(unknown)");
  const groups = new Map();
  for (const j of openJobs) {
    const key = groupKeyFn(j);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(j);
  }
  const groupEntries = Array.from(groups.entries()).map(([key, items]) => {
    sortItems(items, sortBy);
    const priorityStatus = groupPriorityStatus(items);
    return {
      key, items, priorityStatus,
      sortKey: datePostedSortKey(items[0]),
      hasMultiOpening: items.some(j => j.multi_opening),
      inProgress: IN_PROGRESS_OR_NEEDS_ATTENTION.includes(priorityStatus),
    };
  });
  // In-progress/needs-attention groups always float to the top, regardless
  // of sort mode - that's what you actually want to see first.
  groupEntries.sort((a, b) => {
    if (a.inProgress !== b.inProgress) return a.inProgress ? -1 : 1;
    if (sortBy === "multi_opening") {
      if (a.hasMultiOpening !== b.hasMultiOpening) return a.hasMultiOpening ? -1 : 1;
      return b.sortKey - a.sortKey;
    }
    if (sortBy === "company") return a.key.localeCompare(b.key);
    if (sortBy === "status") return statusPriorityIndex(a.items[0].status) - statusPriorityIndex(b.items[0].status);
    if (sortBy === "salary" || sortBy === "salary_asc") {
      const asc = sortBy === "salary_asc";
      const am = jobSalaryDisplay(a.items[0]).min;
      const bm = jobSalaryDisplay(b.items[0]).min;
      const aUnk = am == null;
      const bUnk = bm == null;
      if (aUnk !== bUnk) return aUnk ? 1 : -1;
      if (am !== bm) return asc ? am - bm : bm - am;
      return b.sortKey - a.sortKey;
    }
    return b.sortKey - a.sortKey;
  });

  list.innerHTML = groupEntries.map(({ key, items, priorityStatus, hasMultiOpening }) => {
    if (items.length === 1) {
      return renderJobRow(items[0], {});
    }
    const expanded = expandedCompanies.has(key);
    const latest = items[0];
    // Darker orange on group header when expanded, or when it owns the selection (even collapsed).
    const groupActive = expanded || items.some(j => j.id === selectedId);
    const meta = groupBy === "source"
      ? [`${items.length} roles`]
      : [latest.source, postedMetaLabel(latest) ? `latest ${postedMetaLabel(latest)}` : ""].filter(Boolean);
    if (hasMultiOpening) meta.push("multi openings");
    const dotColor = STATUS_COLORS[priorityStatus] || "#7a828c";
    return `
      <div class="row group-header ${groupActive ? "active" : ""}" data-group="${escapeAttr(key)}" onclick="${escapeAttr(`toggleCompany('${jsStringEscape(key)}')`)}">
        <span class="expand-icon">${expanded ? "▾" : "▸"}</span>
        <div class="company">
          ${!expanded && priorityStatus ? `<span class="status-dot" style="background:${dotColor}" title="${statusLabel(priorityStatus)}"></span>` : ""}
          ${escapeHtml(key) || "(fetching details…)"} <span class="count">${items.length} roles</span>
        </div>
        <div class="meta">${meta.map(m => {
          const cls = m === "multi openings" ? ' class="multi-opening-tag"' : "";
          return `<span${cls}>${escapeHtml(m)}</span>`;
        }).join("")}</div>
      </div>
      ${expanded ? `<div class="group-children">${items.map(job => renderJobRow(job, { nested: true, showCompany: groupBy === "source" })).join("")}</div>` : ""}
    `;
  }).join("") || emptyHtml;
  syncListSelection();
  renderDetail();
}

function formatDate(d) {
  if (!d) return "";
  const t = Date.parse(d);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatDateFull(d) {
  if (!d) return "—";
  const t = Date.parse(d);
  if (Number.isNaN(t)) return escapeHtml(String(d));
  return new Date(t).toLocaleDateString(undefined, {
    month: "short", day: "numeric", year: "numeric",
  });
}

function selectJob(id) {
  selectedId = id;
  const job = jobs.find(j => j.id === id);
  if (job) {
    const groupBy = document.getElementById("group-by")?.value || "none";
    const key = jobGroupKey(job, groupBy);
    if (key) expandedCompanies.add(key);
  }
  activityEvents = [];
  render();
  loadActivity();
  loadJobDescription(id);
}

async function loadJobDescription(jobId) {
  if (!jobId) return;
  const existing = jdCache.get(jobId);
  if (existing && !existing.loading && (existing.text != null || existing.error)) {
    if (selectedId === jobId) renderDetail();
    return;
  }
  jdCache.set(jobId, { loading: true, text: "", error: null, source: null });
  if (selectedId === jobId) renderDetail();
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/description`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      jdCache.set(jobId, {
        loading: false,
        text: "",
        error: data.error || `Failed to load (${res.status})`,
        source: null,
      });
    } else {
      jdCache.set(jobId, {
        loading: false,
        text: data.job_description || "",
        error: null,
        source: data.source || null,
      });
    }
  } catch (e) {
    jdCache.set(jobId, {
      loading: false,
      text: "",
      error: "Failed to load job description",
      source: null,
    });
  }
  if (selectedId === jobId) renderDetail();
}

function jobDescriptionPanelHtml(job) {
  const cached = jdCache.get(job.id);
  // Show panel when we know there is a JD, or while/after a fetch for this selection.
  const expectJd = job.has_description || (cached && (cached.loading || cached.text || cached.error));
  if (!expectJd && !cached) return "";
  if (!cached || cached.loading) {
    return `<div class="panel jd-panel"><div class="panel-title">Job description</div><div class="jd-loading">Loading job description…</div></div>`;
  }
  if (cached.error) {
    return `<div class="panel jd-panel"><div class="panel-title">Job description</div><div class="jd-error">${escapeHtml(cached.error)}</div></div>`;
  }
  if (!cached.text) {
    if (!job.has_description) return "";
    return `<div class="panel jd-panel"><div class="panel-title">Job description</div><div class="jd-empty">No job description available.</div></div>`;
  }
  return `<div class="panel jd-panel"><div class="panel-title">Job description</div><div class="job-description jd-body">${formatJobDescriptionHtml(cached.text)}</div></div>`;
}

function stepperHtml(job) {
  let stageIdx = STAGES.indexOf(job.status);
  if (job.status === "resuming") stageIdx = STAGES.indexOf("filling");
  if (stageIdx === -1) return ""; // stuck/blocked_captcha/cancelled/skipped_* aren't points on this stepper
  return `<div class="stepper">${STAGES.map((s, i) => `
    <div class="step ${i < stageIdx ? "done" : ""} ${i === stageIdx ? "current" : ""}">
      <div class="dot"></div>
      <div class="lbl">${statusLabel(s)}</div>
    </div>
  `).join("")}</div>`;
}

function isAggregatorHost(url) {
  if (!url) return false;
  try {
    const h = new URL(url).hostname.replace(/^www\./, "").toLowerCase();
    return /(linkedin\.com|indeed\.com|glassdoor\.com|ziprecruiter\.com|builtin\.com|monster\.com|dice\.com)/.test(h);
  } catch (_) {
    return false;
  }
}

function applicationHref(job) {
  // Prefer non-aggregator apply_url; fall back to any available link (never hide).
  const candidates = [job.apply_url, job.job_url, job.source_url].filter(Boolean);
  const preferred = candidates.find(u => !isAggregatorHost(u));
  return preferred || candidates[0] || "";
}

function jobSourceNames(job) {
  const names = [];
  const seen = new Set();
  const push = (n) => {
    const s = String(n || "").trim();
    if (!s) return;
    const key = s.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    names.push(s);
  };
  if (Array.isArray(job.source_names)) job.source_names.forEach(push);
  if (Array.isArray(job.sources)) {
    job.sources.forEach(s => push(s && (s.name || s.id)));
  }
  push(job.source);
  return names;
}

/** Equality key for apply/alt URLs: lowercased host+path (Ashby org slug twins, etc.). */
function normalizeApplyUrlKey(url) {
  if (!url) return "";
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "").toLowerCase();
    const path = (u.pathname || "").replace(/\/$/, "").toLowerCase();
    return `${u.protocol}//${host}${path}${u.search || ""}`;
  } catch (_) {
    return String(url).replace(/\/$/, "").toLowerCase();
  }
}

function applyUrlHost(url) {
  if (!url) return "";
  try { return new URL(url).hostname.replace(/^www\./, "").toLowerCase(); } catch (_) { return ""; }
}

function preferSecondaryApplyUrl(existing, candidate) {
  if (!existing) return candidate;
  if (!candidate) return existing;
  const score = (u) => {
    const s = String(u || "").toLowerCase();
    let n = 0;
    if (/\/application\/?(\?|$)/.test(s)) n += 2;
    if (/\/apply\/?(\?|$)/.test(s)) n += 1;
    return n;
  };
  return score(candidate) > score(existing) ? candidate : existing;
}

function secondaryApplyLinks(job) {
  const primary = applicationHref(job);
  const primaryKey = normalizeApplyUrlKey(primary);
  const primaryHost = applyUrlHost(primary);
  const links = [];
  const seenUrls = new Set();
  const seenLabels = new Set();
  const coveredHosts = new Set();
  if (primaryHost) coveredHosts.add(primaryHost);

  const add = (url, explicitLabel) => {
    if (!url) return;
    const key = normalizeApplyUrlKey(url);
    if (!key || key === primaryKey || seenUrls.has(key)) return;
    const hostname = applyUrlHost(url);
    if (hostname && coveredHosts.has(hostname)) return;

    const label = (explicitLabel && String(explicitLabel).trim())
      || hostname
      || "link";
    const labelKey = label.toLowerCase();
    if (seenLabels.has(labelKey)) {
      const idx = links.findIndex(l => String(l.label).toLowerCase() === labelKey);
      if (idx >= 0) {
        links[idx].url = preferSecondaryApplyUrl(links[idx].url, url);
        seenUrls.add(key);
      }
      return;
    }

    seenUrls.add(key);
    seenLabels.add(labelKey);
    if (hostname) coveredHosts.add(hostname);
    links.push({ url, label });
  };
  if (Array.isArray(job.sources)) {
    for (const s of job.sources) {
      if (!s) continue;
      add(s.apply_url || s.job_url, s.name || s.id);
    }
  }
  for (const u of [job.job_url, job.source_url, ...(job.alternate_urls || [])]) add(u);
  return links;
}

const DISCOVER_ICON_SVG = `<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
  <path fill="currentColor" d="M8 1a.75.75 0 0 1 .75.75V9.4l2.1-2.1a.75.75 0 1 1 1.06 1.06l-3.4 3.4a.75.75 0 0 1-1.06 0l-3.4-3.4A.75.75 0 0 1 5.1 7.3l2.15 2.15V1.75A.75.75 0 0 1 8 1zM2.5 12.25a.75.75 0 0 1 .75-.75h9.5a.75.75 0 0 1 0 1.5H3.25a.75.75 0 0 1-.75-.75z"/>
</svg>`;

function viaAggregatorNote(job) {
  const apply = applicationHref(job);
  const via = job.source_url || (isAggregatorHost(job.job_url) ? job.job_url : "");
  if (!apply || !via || !isAggregatorHost(via) || isAggregatorHost(apply)) return "";
  try {
    const host = new URL(via).hostname.replace(/^www\./, "");
    if (host.includes("linkedin")) return ` <span style="opacity:0.7;font-size:12px">(via LinkedIn)</span>`;
    if (host.includes("indeed")) return ` <span style="opacity:0.7;font-size:12px">(via Indeed)</span>`;
    return ` <span style="opacity:0.7;font-size:12px">(via ${host})</span>`;
  } catch (_) {
    return "";
  }
}

function renderDetail() {
  const detail = document.getElementById("detail");
  const showAppliedTable = statusFilterMode === "applied";
  const job = jobs.find(j => j.id === selectedId);
  const showJobDetail = !!(job && (!showAppliedTable || job.status === "applied"));

  if (!showJobDetail) {
    detail.innerHTML = showAppliedTable
      ? renderAppliedTableHtml()
      : '<div class="empty">Select a job to see its live status.</div>';
    return;
  }

  const color = STATUS_COLORS[job.status] || "#7a828c";
  const blocked = ["stuck", "blocked_captcha"].includes(job.status);
  const appHref = applicationHref(job);
  const delReasonsLong = job.status === "deleted" ? formatDeletedReasons(job, { short: false }) : null;
  const delReasonsShort = job.status === "deleted" ? formatDeletedReasons(job, { short: true }) : null;
  const statusBadgeText = job.status === "deleted"
    ? (delReasonsShort ? `Deleted · ${delReasonsShort}` : "Deleted")
    : statusLabel(job.status);
  let html = showAppliedTable ? renderAppliedTableHtml() : "";
  if (showAppliedTable) html += '<div id="job-detail-anchor">';
  html += `
    <h2>${job.company || "(fetching details…)"} — ${job.title || ""}</h2>
    <div class="subhead">${(() => {
      const bits = [job.location, jobSourceNames(job).join(", "), postedMetaLabel(job)].filter(Boolean);
      const { mode, approx: modeApprox } = jobWorkModeDisplay(job);
      if (mode && mode !== "unknown") bits.push(formatWorkMode(mode, modeApprox));
      const { n: ymin, approx } = jobMinYoeDisplay(job);
      const yoe = ymin != null && !Number.isNaN(Number(ymin)) ? formatYoeLabel(ymin, approx) : "";
      if (yoe) bits.push(yoe);
      const { min: sMin, max: sMax, approx: sApprox } = jobSalaryDisplay(job);
      const sal = formatSalaryLabel(sMin, sMax, { approx: sApprox, compact: true });
      if (sal) bits.push(sal);
      return bits.join(" · ");
    })()} &nbsp;·&nbsp;
      <span class="badge" style="background:${color}22;color:${color}">${escapeHtml(statusBadgeText)}</span>
    </div>
    ${job.status === "deleted" ? `<p class="deleted-reason-line"><strong>Deleted reason:</strong> ${escapeHtml(delReasonsLong || "No reason recorded")}${job.deleted_at ? ` · ${escapeHtml(formatDate(job.deleted_at))}` : ""}</p>` : ""}
    ${stepperHtml(job)}
    <p>${job.status_detail || ""}</p>
    <p>
      ${appHref ? `<a href="${appHref}" target="_blank">Application link ↗</a>${viaAggregatorNote(job)}` : "<span style=\"opacity:0.7\">No application link yet</span>"}
    </p>
    ${(() => {
      const alts = secondaryApplyLinks(job);
      if (!alts.length) return "";
      return `<div class="alt-links"><span style="opacity:0.7;font-size:11px">Also</span>${alts.map(l =>
        `<a href="${escapeHtml(l.url)}" target="_blank" rel="noopener" title="${escapeHtml(l.url)}">${escapeHtml(l.label)}</a>`
      ).join("")}</div>`;
    })()}
    ${jobDescriptionPanelHtml(job)}
  `;

  if (job.pending_command) {
    html += `
      <div class="panel command-box">
        <div class="panel-title">Agent wants to run a command not on its allowlist</div>
        <pre class="command">${escapeHtml(job.pending_command)}</pre>
        <div style="opacity:0.75">Approving remembers this command for next time (scoped to this agent only).</div>
        <div class="btn-row">
          <button class="primary" onclick="${escapeAttr(`decideCommand('${jsStringEscape(job.id)}', true)`)}">Approve &amp; remember</button>
          <button class="danger" onclick="${escapeAttr(`decideCommand('${jsStringEscape(job.id)}', false)`)}">Deny</button>
        </div>
      </div>
    `;
  } else if (blocked) {
    html += `
      <div class="panel command-box">
        <div class="panel-title">Agent needs your help</div>
        <div>${escapeHtml(job.question || "(no question recorded)")}</div>
        <textarea id="answer" rows="3" placeholder="Type your answer..."></textarea>
        <div class="btn-row"><button class="primary" onclick="${escapeAttr(`submitAnswer('${jsStringEscape(job.id)}')`)}">Send answer</button></div>
      </div>
    `;
  }

  const jid = jsStringEscape(job.id);
  const runInProgress = ["tailoring", "navigating", "filling", "resuming"].includes(job.status);
  const otherBusy = jobs.some(j =>
    j && j.id !== job.id && (
      ["tailoring", "navigating", "filling", "resuming"].includes(j.status)
      || (fillHoldActive && ["ready_for_review", "blocked_captcha"].includes(j.status))
    )
  );
  html += `<div class="btn-row">`;
  // DASH2-003: Restore for deleted / skipped (API already supports).
  const canRestore = job.status === "deleted"
    || ["skipped_manual", "skipped_duplicate", "skipped_contract", "skipped_easy_apply", "cancelled"].includes(job.status);
  if (canRestore) {
    // UI-016: Restore is the sole primary recovery for cancelled/skipped/deleted.
    html += `<button class="primary" onclick="${escapeAttr(`restoreJob('${jid}')`)}">Restore</button>`;
  } else if (["discovered", "stuck", "blocked_captcha"].includes(job.status) && !otherBusy) {
    html += `<button class="primary" onclick="${escapeAttr(`startJob('${jid}')`)}">${job.status === "discovered" ? "Fill" : "Retry"}</button>`;
  }
  // Fast fill — dummy when Test Mode ON, real profile when OFF.
  const isFastFillRun = (job.status_detail || "").includes("[DUMMY/TEST]")
    || (job.status_detail || "").includes("[REAL]");
  // UI-003: block Ready/CAPTCHA; UI-008: block when another job held/busy.
  const canFastFill = !["tailoring", "navigating", "filling", "resuming", "applied", "ready_for_review", "blocked_captcha"].includes(job.status)
    && !canRestore
    && !otherBusy
    && !!applicationHref(job);
  if (canFastFill) {
    const fillLabel = testModeEnabled ? "Fast fill (dummy)" : "Fast fill (real)";
    const fillTitle = testModeEnabled
      ? "Test Mode ON: DUMMY_PROFILE + dummy resume. Never submits."
      : "Test Mode OFF: real profile.json + resume PDF. Never auto-submits.";
    html += `<button class="test-dummy${testModeEnabled ? "" : " real-fill"}" onclick="${escapeAttr(`hybridFillDummy('${jid}')`)}" title="${escapeAttr(fillTitle)}">${fillLabel}</button>`;
  }
  if (job.status === "ready_for_review") {
    html += `<button class="primary" onclick="${escapeAttr(`markSubmitted('${jid}')`)}">Mark as applied</button>`;
  }
  if (!TERMINAL.includes(job.status) && !canRestore) {
    html += `<button onclick="${escapeAttr(`skipJob('${jid}')`)}">Skip</button>`;
  }
  // Cancel only while a run is in progress (not Ready / discovered / stuck).
  if (runInProgress) {
    if (job.status === "filling" && isFastFillRun) {
      html += `<button class="danger" onclick="${escapeAttr(`cancelJob('${jid}')`)}">Cancel fast fill</button>`;
    } else {
      html += `<button class="danger" onclick="${escapeAttr(`cancelJob('${jid}')`)}">Cancel run</button>`;
    }
  }
  if (job.resume_path) {
    html += `<a class="btn-link" href="/resume/${encodeURIComponent(job.id)}" target="_blank">View Resume</a>`;
  }
  if (!runInProgress) {
    html += `<label class="btn-link" style="cursor:pointer">Upload resume<input type="file" accept=".pdf,.doc,.docx" hidden onchange="uploadJobResume('${jid}', this)"></label>`;
  }
  if (job.status !== "ready_for_review" && job.status !== "applied" && !["tailoring","navigating","filling","resuming"].includes(job.status)) {
    html += `<button onclick="${escapeAttr(`markSubmitted('${jid}')`)}">Mark as applied</button>`;
  }
  html += `<button onclick="${escapeAttr(`deleteJob('${jid}')`)}">Delete</button>`;
  html += `</div>`;

  if (isFastFillRun) {
    const isDummyRun = (job.status_detail || "").includes("[DUMMY/TEST]");
    html += `
      <div class="panel dummy-test-box${isDummyRun ? "" : " real-fill-box"}">
        <div class="panel-title">${isDummyRun ? "Dummy / test fill" : "Real-profile fast fill"}</div>
        <div>${escapeHtml(job.status_detail)}</div>
        <div style="margin-top:8px;color:var(--text-dim);font-size:11.5px">
          ${isDummyRun
            ? "Uses fixture dummy resume + DUMMY_PROFILE only — not your real profile.json."
            : "Uses real profile.json + tailored/trusted resume — production apply prep."}
          Never auto-submits. <strong>Start</strong> is PartyRock tailor then Playwright fast fill.
        </div>
      </div>
    `;
  }

  html += `<div class="panel"><div class="panel-title">Live activity</div><div class="feed" id="activity-feed"></div></div>`;

  if (job.qa_log && job.qa_log.length) {
    html += '<div class="panel"><div class="panel-title">History</div><div class="qa-log">';
    for (const qa of job.qa_log) {
      html += `<div class="item"><div class="q">${escapeHtml(qa.question || "")}</div><div class="a">→ ${escapeHtml(qa.answer)}</div></div>`;
    }
    html += "</div></div>";
  }

  if (showAppliedTable) html += "</div>";
  detail.innerHTML = html;
  renderActivityFeed();
  if (scrollToAppliedDetail) {
    scrollToAppliedDetail = false;
    requestAnimationFrame(() => {
      document.getElementById("job-detail-anchor")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
}

function renderActivityFeed() {
  const el = document.getElementById("activity-feed");
  if (!el) return;
  if (!activityEvents.length) {
    el.innerHTML = '<div style="color:var(--text-dim)">No activity recorded yet.</div>';
    return;
  }
  el.innerHTML = activityEvents.map(e => `
    <div class="ev"><span class="t">${e.time}</span><span class="k">${escapeHtml(e.event || "")}</span><span class="d">${escapeHtml(e.detail || "")}</span></div>
  `).join("");
  el.scrollTop = el.scrollHeight;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

/** Inline **bold** / *italic* on already-classified line text (escaped). */
function formatJdInline(raw) {
  let s = escapeHtml(raw == null ? "" : String(raw));
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong class="jd-strong">$1</strong>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em class="jd-em">$2</em>');
  return s;
}

/**
 * Classify a JD line for the Evidence panel subset renderer.
 * Returns { type: 'blank'|'heading'|'bullet'|'text', text?: string }.
 */
function classifyJdLine(line) {
  const t = String(line || "").trim();
  if (!t) return { type: "blank" };

  let m = t.match(/^(#{1,3})\s+(.+?)\s*#*\s*$/);
  if (m) return { type: "heading", text: m[2].trim() };

  m = t.match(/^\*\*(.+?)\*\*\s*:?\s*$/);
  if (m) return { type: "heading", text: m[1].trim() };

  m = t.match(/^([*\-•★●◦])\s+(.+)$/);
  if (m) {
    const rest = m[2].trim();
    // "* Must-haves:" / "* REQUIRED" → section heading, not a bullet
    if (/^.{1,60}:\s*$/.test(rest)) {
      return { type: "heading", text: rest.replace(/\s+$/, "") };
    }
    if (
      rest.length >= 3 &&
      rest.length <= 60 &&
      /^[A-Z0-9][A-Z0-9 &/\-',.:()]+$/.test(rest) &&
      /[A-Z]{3,}/.test(rest) &&
      !/[.!?]$/.test(rest)
    ) {
      return { type: "heading", text: rest };
    }
    return { type: "bullet", text: rest };
  }

  // ALL CAPS short labels: WHAT WE'RE LOOKING FOR
  if (
    t.length >= 3 &&
    t.length <= 60 &&
    /^[A-Z0-9][A-Z0-9 &/\-',.:()]+$/.test(t) &&
    /[A-Z]{3,}/.test(t) &&
    !/[.!?]$/.test(t.replace(/:\s*$/, ""))
  ) {
    return { type: "heading", text: t };
  }

  // Short label ending with colon: Required: / Must-haves:
  if (
    t.length <= 60 &&
    /:\s*$/.test(t) &&
    t.split(/\s+/).length <= 8 &&
    !/^[*\-•★]/.test(t)
  ) {
    return { type: "heading", text: t };
  }

  return { type: "text", text: t };
}

/**
 * Safe structured HTML for JD text (escape + tiny markdown/ATS subset).
 * No raw HTML passthrough — only tags we emit after escaping.
 */
function formatJobDescriptionHtml(text) {
  if (text == null || text === "") return "";
  const lines = String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const out = [];
  let paraLines = [];
  let bullets = [];

  const flushPara = () => {
    if (!paraLines.length) return;
    const body = paraLines.map(formatJdInline).join("<br>");
    out.push(`<p class="jd-p">${body}</p>`);
    paraLines = [];
  };
  const flushList = () => {
    if (!bullets.length) return;
    const items = bullets.map(b => `<li>${formatJdInline(b)}</li>`).join("");
    out.push(`<ul class="jd-list">${items}</ul>`);
    bullets = [];
  };

  for (const line of lines) {
    const c = classifyJdLine(line);
    if (c.type === "blank") {
      flushList();
      flushPara();
      continue;
    }
    if (c.type === "heading") {
      flushList();
      flushPara();
      out.push(`<div class="jd-heading">${formatJdInline(c.text)}</div>`);
      continue;
    }
    if (c.type === "bullet") {
      flushPara();
      bullets.push(c.text);
      continue;
    }
    flushList();
    paraLines.push(c.text);
  }
  flushList();
  flushPara();
  return out.join("");
}

async function submitAnswer(jobId) {
  const answer = document.getElementById("answer").value.trim();
  if (!answer) return;
  await fetch(`/api/jobs/${encodeURIComponent(jobId)}/answer`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  await poll();
}

async function decideCommand(jobId, approve) {
  await fetch(`/api/jobs/${encodeURIComponent(jobId)}/approve_command`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approve }),
  });
  await poll();
}

async function startJob(jobId) {
  const testMode = testModeEnabled;
  const skipPartyrock = testMode && !partyRockEnabled;
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/start`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({
      test_mode: testMode,
      skip_partyrock: skipPartyrock,
      partyrock: !skipPartyrock,
    }),
  });
  if (res.status === 409) {
    const d = await res.json().catch(() => ({}));
    alert(d.error || "Can't start — another job is already running (one fill at a time).");
  } else if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    alert(d.error || `Fill failed (${res.status})`);
  } else {
    try {
      const d = await res.json();
      if (d.skip_partyrock) {
        console.log(
          `[Fill] test_mode=${d.test_mode} PartyRock bypassed → ${d.fill_after_tailor}`
        );
      } else if (d.partyrock_url) {
        console.log(
          `[PartyRock] Fill test_mode=${d.test_mode} mode=${d.partyrock_mode} url=${d.partyrock_url}`
        );
      }
    } catch (_) { /* ignore */ }
  }
  await poll();
}

async function hybridFillDummy(jobId) {
  const job = jobs.find(j => j.id === jobId);
  if (job && ["ready_for_review", "blocked_captcha"].includes(job.status)) {
    alert("Fast fill blocked while Ready/CAPTCHA — Mark as applied or close the fill browser first.");
    return;
  }
  const testMode = testModeEnabled;
  const ok = testMode
    ? confirm(
      "Run FAST FILL (TEST MODE)?\n\n"
      + "• Uses DUMMY_PROFILE + dummy resume fixture only\n"
      + "• Does NOT use your real profile.json or tailored resume\n"
      + "• NEVER submits the application\n\n"
      + "Continue?"
    )
    : confirm(
      "Run FAST FILL with REAL DATA?\n\n"
      + "Test Mode is OFF — this will use:\n"
      + "• Your real profile.json contact + credentials\n"
      + "• Tailored resume (resumes/<job>/resume.pdf) or trusted_uploads/resume.pdf\n"
      + "• Still NEVER auto-submits — you submit manually in the browser\n\n"
      + "Continue?"
    );
  if (!ok) return;
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/hybrid_fill_dummy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ test_mode: testMode }),
  });
  if (res.status === 409) {
    const d = await res.json();
    alert(d.error || "Can't start fast fill — another job is already running (one fill at a time).");
  } else if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    alert(d.error || `Fast fill failed (${res.status})`);
  }
  lastJobsJSON = null;
  await poll();
}

function toggleTestMode() {
  testModeEnabled = !testModeEnabled;
  saveTestModeSetting(testModeEnabled);
  syncTestModeToggleUI();
  render();
}

function togglePartyRock() {
  if (!testModeEnabled) {
    syncTestModeToggleUI();
    return;
  }
  partyRockEnabled = !partyRockEnabled;
  savePartyRockSetting(partyRockEnabled);
  syncTestModeToggleUI();
}

function syncTestModeToggleUI() {
  const el = document.getElementById("test-mode-toggle");
  if (el) {
    el.classList.toggle("test-on", testModeEnabled);
    el.classList.toggle("test-off", !testModeEnabled);
    el.setAttribute("aria-pressed", testModeEnabled ? "true" : "false");
    el.setAttribute("aria-label", testModeEnabled ? "Test mode on" : "Test mode off");
    el.title = testModeEnabled
      ? "Test mode ON: dummy profile + dummy resume. Fast fill never auto-submits."
      : "Test mode OFF: real profile. Fast fill never auto-submits.";
  }
  const label = document.getElementById("test-mode-label");
  if (label) {
    label.textContent = testModeEnabled ? "Test mode on" : "Test mode off";
  }
  const prEl = document.getElementById("partyrock-toggle");
  if (prEl) {
    prEl.checked = partyRockEnabled;
    prEl.disabled = !testModeEnabled;
  }
  const prLabel = document.getElementById("partyrock-label");
  if (prLabel) {
    prLabel.textContent = partyRockEnabled ? "PartyRock on" : "PartyRock off";
    prLabel.classList.toggle("pr-on", partyRockEnabled);
    prLabel.classList.toggle("pr-off", !partyRockEnabled);
  }
  const prOpt = document.getElementById("partyrock-opt");
  if (prOpt) {
    prOpt.classList.toggle("disabled", !testModeEnabled);
  }
  const hint = document.getElementById("partyrock-hint");
  if (hint) {
    if (!testModeEnabled) {
      hint.textContent = "Enable Test Mode to control PartyRock for Start.";
    } else if (partyRockEnabled) {
      hint.textContent =
        "Start uses PartyRock Testing, then dummy fill. Turn off to skip tailor and go straight to dummy fill.";
    } else {
      hint.textContent =
        "Start skips PartyRock and fills with dummy resume + DUMMY_PROFILE only. Never submits.";
    }
  }
}

async function cancelJob(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: "{}",
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    alert(d.error || `Cancel failed (${res.status})`);
  }
  await poll();
}

async function skipJob(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/skip`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: "{}",
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    alert(d.error || `Skip failed (${res.status})`);
  }
  await poll();
}

async function restoreJob(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/restore`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: "{}",
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    alert(d.error || `Restore failed (${res.status})`);
  }
  await poll();
}

async function markSubmitted(jobId) {
  const job = jobs.find(j => j.id === jobId);
  const status = job?.status || "";
  const msg = status === "ready_for_review"
    ? "Mark this job as applied? (You submit on the employer site — we never auto-submit.)"
    : `This job is not Ready yet (status: ${statusLabel(status) || status || "unknown"}).\n\n`
      + "Mark as applied anyway? Only do this if you already submitted on the employer site. "
      + "We never auto-submit.";
  if (!confirm(msg)) return;
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/submitted`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: "{}",
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    alert(d.error || `Mark applied failed (${res.status})`);
  }
  await poll();
}

async function uploadJobResume(jobId, inputEl) {
  const file = inputEl && inputEl.files && inputEl.files[0];
  if (!file) return;
  const job = jobs.find(j => j.id === jobId);
  if (job && ["tailoring", "navigating", "filling", "resuming"].includes(job.status)) {
    alert("Resume upload blocked while fill/tailor is running. Cancel first.");
    try { inputEl.value = ""; } catch (_) {}
    return;
  }
  const fd = new FormData();
  fd.append("resume", file, file.name);
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/resume`, {
      method: "POST",
      body: fd,
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) alert(d.error || `Upload failed (${res.status})`);
  } catch (e) {
    alert(`Upload failed: ${e}`);
  } finally {
    try { inputEl.value = ""; } catch (_) {}
  }
  await poll();
}

async function deleteJob(jobId) {
  const job = jobs.find(j => j.id === jobId);
  const msg = job && job.status === "applied"
    ? "Delete this applied job from tracking?\n\nIt moves to Deleted (soft). URL tombstones still block rediscovery."
    : "Move to Deleted?";
  if (!confirm(msg)) return;
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    alert(d.error || `Delete failed (${res.status})`);
    return;
  }
  const j = jobs.find(x => x.id === jobId);
  if (j) {
    j.status = "deleted";
    j.deleted_reason = "user";
    j.deleted_at = new Date().toISOString();
    j.updated_at = j.deleted_at;
  }
  if (selectedId === jobId && statusFilterMode !== "deleted") selectedId = null;
  lastJobsJSON = JSON.stringify(jobs);
  render();
  await poll();
}

function setAddJobPopoverOpen(open) {
  const wrap = document.getElementById("add-job-wrap");
  const btn = document.getElementById("add-job-btn");
  if (!wrap || !btn) return;
  wrap.classList.toggle("open", !!open);
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    requestAnimationFrame(() => document.getElementById("add-job-url")?.focus());
  } else {
    const ae = document.activeElement;
    if (ae && wrap.contains(ae) && typeof ae.blur === "function") ae.blur();
  }
}

function addJobPopoverIsVisible() {
  const wrap = document.getElementById("add-job-wrap");
  if (!wrap) return false;
  if (wrap.classList.contains("open")) return true;
  try {
    return wrap.matches(":hover") || wrap.matches(":focus-within");
  } catch (_) {
    return false;
  }
}

async function addJobByUrl() {
  const input = document.getElementById("add-job-url");
  const url = input.value.trim();
  if (!url) return;
  const res = await fetch("/api/jobs/add", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "Could not add job.");
    return;
  }
  input.value = "";
  setAddJobPopoverOpen(false);
  await poll();
  selectJob(data.id);
}

document.getElementById("add-job-url")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); addJobByUrl(); }
});
document.getElementById("add-job-btn")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const wrap = document.getElementById("add-job-wrap");
  setAddJobPopoverOpen(!wrap?.classList.contains("open"));
});
document.getElementById("add-job-wrap")?.addEventListener("mouseenter", () => {
  const input = document.getElementById("add-job-url");
  if (input && document.activeElement !== input) {
    requestAnimationFrame(() => input.focus());
  }
});
document.getElementById("add-job-popover")?.addEventListener("click", (e) => e.stopPropagation());

(function bindClassicFilters() {
  const filterRow = document.getElementById("filter-row");
  const toggle = document.getElementById("filters-toggle");
  const setOpen = (open) => {
    if (!filterRow || !toggle) return;
    filterRow.classList.toggle("open", !!open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (!open) {
      const ae = document.activeElement;
      if (ae && filterRow.contains(ae) && typeof ae.blur === "function") ae.blur();
    }
  };
  const isVisible = () => {
    if (!filterRow) return false;
    if (filterRow.classList.contains("open")) return true;
    try {
      return filterRow.matches(":hover") || filterRow.matches(":focus-within");
    } catch (_) {
      return false;
    }
  };
  const activeCount = () => {
    let n = 0;
    if ((document.getElementById("filter-input")?.value || "").trim()) n++;
    if (document.getElementById("source-filter")?.value) n++;
    if ((document.getElementById("group-by")?.value || "none") !== "none") n++;
    if ((document.getElementById("sort-by")?.value || "date") !== "date") n++;
    return n;
  };
  const updateChrome = () => {
    const n = activeCount();
    const label = document.getElementById("filters-toggle-label");
    const clearBtn = document.getElementById("filters-popover-clear");
    if (label) label.textContent = n > 0 ? `Filters · ${n}` : "Filters";
    toggle?.classList.toggle("has-active", n > 0);
    if (clearBtn) clearBtn.disabled = n === 0;
  };
  const clearFilters = () => {
    const input = document.getElementById("filter-input");
    if (input) input.value = "";
    const source = document.getElementById("source-filter");
    if (source) source.value = "";
    const group = document.getElementById("group-by");
    if (group) group.value = "none";
    const sort = document.getElementById("sort-by");
    if (sort) sort.value = "date";
    updateChrome();
    render();
  };

  let searchTimer;
  const filterInput = document.getElementById("filter-input");
  filterInput?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      updateChrome();
      render();
    }, 180);
  });
  filterInput?.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    e.preventDefault();
    e.stopPropagation();
    if (!filterInput.value) return;
    filterInput.value = "";
    updateChrome();
    render();
  });
  for (const id of ["source-filter", "group-by", "sort-by"]) {
    document.getElementById(id)?.addEventListener("change", () => {
      updateChrome();
      render();
    });
  }
  // Hover/focus-within via CSS; click pins for touch/keyboard.
  toggle?.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(!filterRow?.classList.contains("open"));
  });
  document.getElementById("filters-popover")?.addEventListener("click", (e) => e.stopPropagation());
  document.getElementById("filters-popover-clear")?.addEventListener("click", clearFilters);
  document.addEventListener("mousedown", (e) => {
    if (filterRow?.classList.contains("open") && !filterRow.contains(e.target)) {
      setOpen(false);
    }
    const addJob = document.getElementById("add-job-wrap");
    if (addJob?.classList.contains("open") && !addJob.contains(e.target)) {
      setAddJobPopoverOpen(false);
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (addJobPopoverIsVisible()) {
      e.preventDefault();
      setAddJobPopoverOpen(false);
      return;
    }
    if (document.activeElement?.id === "filter-input") return;
    if (!isVisible()) return;
    e.preventDefault();
    setOpen(false);
  });
  updateChrome();
  // Expose for render() so badge stays in sync after list rebuilds.
  window.__classicUpdateFiltersChrome = updateChrome;
})();

async function runDiscover(fresh = false) {
  const btn = document.getElementById("discover-btn");
  if (discoveryState && discoveryState.running) return;
  const sources = enabledDiscoverySourceIds();
  if (!sources.length) {
    alert("Enable at least one discovery source (hover Discover).");
    return;
  }
  if (fresh && !confirm("Start a fresh discovery pass? Clears the incomplete checkpoint; still skips URLs already in jobs.json.")) {
    return;
  }
  btn.disabled = true;
  btn.classList.add("running");
  btn.innerHTML = `<span class="btn-spinner" aria-hidden="true"></span>`;
  const startLabel = fresh
    ? "Starting fresh…"
    : ((discoveryState && discoveryState.resume_available) ? "Continuing previous run…" : "Discovering…");
  btn.title = startLabel;
  btn.setAttribute("aria-label", startLabel);
  const res = await fetch("/api/discover", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ sources, fresh: !!fresh }),
  });
  const d = await res.json().catch(() => ({}));
  if (res.status === 409) {
    alert(d.error || "Can't run discovery - it's already running.");
  } else if (res.status === 400) {
    alert(d.error || "Enable at least one discovery source.");
  }
  if (d.discovery) discoveryState = d.discovery;
  syncDiscoverUI();
  await pollStatus();
  await poll();
}

async function abortDiscover() {
  const res = await fetch("/api/discover/abort", {
    method: "POST", headers: {"Content-Type":"application/json"}, body: "{}",
  });
  const d = await res.json().catch(() => ({}));
  if (d.discovery) discoveryState = d.discovery;
  syncDiscoverUI();
  await pollStatus();
}

let discoveryState = null;
let lastRuntimeJSON = null;

function sourceStatusLabel(s) {
  return ({ pending: "pending", collecting: "collecting", completed: "done",
    stopped: "stopped", failed: "failed", skipped: "off" })[s] || s || "pending";
}

function renderDiscoverPopover(disc) {
  const el = document.getElementById("discover-popover");
  if (!el) return;
  const enabledMap = loadDiscoverySourceSettings();
  const runSources = (disc && disc.sources) || [];
  const byId = Object.fromEntries(runSources.map(s => [s.id, s]));
  const catalog = (disc && disc.source_catalog && disc.source_catalog.length)
    ? disc.source_catalog
    : DISCOVERY_SOURCE_CATALOG;
  const running = !!(disc && disc.running);
  const hasRun = runSources.length > 0;
  const activeForPct = runSources.filter(s => s.status !== "skipped");
  const done = activeForPct.filter(s =>
    s.status === "completed" || s.status === "failed" || s.status === "stopped").length;
  const pct = activeForPct.length ? Math.round((done / activeForPct.length) * 100) : 0;
  const phase = running
    ? (disc.phase_label || (disc.resumed ? "Continuing previous run…" : "Discovering…"))
    : (disc && disc.resume_available
      ? (disc.error || "Incomplete — click Discover to continue")
      : (hasRun
        ? (disc.ok === true ? "Completed" : (disc.error || "Finished"))
        : "Toggle sources, then Discover"));
  const lastRun = lastDiscoverRunLabel(disc);
  const total = runSources.reduce((n, s) => n + (s.count || 0), 0);
  const rows = catalog.map(c => {
    const on = enabledMap[c.id] !== false;
    const live = byId[c.id];
    const st = live ? (live.status || "pending") : (on ? "idle" : "skipped");
    const count = live && live.count != null ? live.count : "—";
    const detail = live ? (live.detail || "") : (on ? "" : "Disabled");
    const showBar = !!(live && running);
    return `<label class="discover-src-opt ${on ? "src-on" : "src-off"} ${escapeHtml(st)}">
      <input type="checkbox" data-source-id="${escapeHtml(c.id)}" ${on ? "checked" : ""}
        onchange="toggleDiscoverySource(this.dataset.sourceId, this.checked)">
      <span class="src-label ${on ? "src-on" : "src-off"}">${escapeHtml(c.label || c.id)}</span>
      <span class="src-count">${count}</span>
      <div class="src-meta">
        <span class="src-status">${escapeHtml(sourceStatusLabel(st === "idle" ? "pending" : st))}</span>
        ${showBar ? `<span class="src-bar"><span></span></span>` : ""}
        <span>${escapeHtml(detail)}</span>
      </div>
    </label>`;
  }).join("");
  el.innerHTML = `
    <div class="pop-head">
      <div class="pop-title">Discovery sources</div>
      <div class="pop-phase">${escapeHtml(phase)}</div>
    </div>
    ${lastRun ? `<div class="pop-hint discover-last-run" style="margin:0 0 8px">${escapeHtml(lastRun)}</div>` : ""}
    ${hasRun || running ? `<div class="pop-overall"><span style="width:${pct}%"></span></div>` : ""}
    ${rows}
    <div class="pop-actions">
      <span style="flex:1;color:var(--text-dim);font-size:11px">${
        hasRun || running ? `${total} collected` : "Choices saved locally"
      }</span>
      ${!running
        ? `<button type="button" onclick="runDiscover(true)" title="Clear checkpoint and start a new pass (still skips known URLs)">Fresh run</button>`
        : ""}
      ${running && disc.can_abort !== false
        ? `<button type="button" onclick="abortDiscover()" style="color:var(--red);border-color:#e0555555">Abort</button>`
        : ""}
    </div>`;
}

function formatDiscoverAge(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const ms = Date.now() - t;
  if (ms < 0) return null;
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatDiscoverWhen(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  try {
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    });
  } catch (_) {
    return formatDiscoverAge(iso);
  }
}

function lastDiscoverRunLabel(disc) {
  if (!disc) return null;
  const outcome = disc.last_outcome
    || (disc.resume_available ? "interrupted"
      : (disc.ok === true ? "success" : (disc.ok === false ? "failed" : null)));
  if (!outcome && !disc.last_finished_at && !disc.finished_at) return null;
  const when = formatDiscoverWhen(disc.last_finished_at || disc.finished_at)
    || formatDiscoverAge(disc.last_finished_at || disc.finished_at);
  const parts = [`Last run: ${outcome || "unknown"}`];
  if (when) parts.push(when);
  if (disc.last_summary) parts.push(disc.last_summary);
  else if (disc.last_jobs_added != null && disc.last_jobs_added !== "") {
    const n = Number(disc.last_jobs_added);
    if (!Number.isNaN(n)) parts.push(n ? `+${n} jobs` : "+0 jobs");
  }
  if (disc.resume_available && outcome !== "interrupted") parts.push("will continue");
  return parts.join(" · ");
}

function discoverButtonIdleLabel(disc) {
  if (disc && disc.resume_available) return "Discover · continue";
  const age = formatDiscoverAge(disc && (disc.last_finished_at || disc.finished_at));
  const outcome = disc && disc.last_outcome;
  if (outcome && age) return `Discover · ${outcome} · ${age}`;
  return age ? `Discover · ${age}` : "Discover";
}

function syncDiscoverUI() {
  const btn = document.getElementById("discover-btn");
  const abortBtn = document.getElementById("discover-abort-btn");
  const wrap = document.getElementById("discover-wrap");
  const disc = discoveryState;
  const running = !!(disc && disc.running);
  if (btn) {
    if (running) {
      btn.disabled = true;
      btn.classList.add("running");
      const runLabel = (disc && disc.resumed) ? "Continuing previous run…" : "Discovering…";
      btn.innerHTML = `<span class="btn-spinner" aria-hidden="true"></span>`;
      btn.title = disc.phase_label || runLabel;
      btn.setAttribute("aria-label", runLabel);
    } else {
      btn.disabled = false;
      btn.classList.remove("running");
      btn.innerHTML = DISCOVER_ICON_SVG;
      const age = formatDiscoverAge(disc && (disc.last_finished_at || disc.finished_at));
      const idle = discoverButtonIdleLabel(disc);
      btn.setAttribute("aria-label", idle);
      if (disc && disc.resume_available) {
        btn.title = (lastDiscoverRunLabel(disc) || "Incomplete discovery") + " — click to continue leftover sources";
      } else {
        btn.title = age
          ? `Last discovery ${age}. Hover to enable/disable sources, then run discovery.`
          : "Hover to enable/disable sources, then run discovery";
      }
    }
  }
  if (abortBtn) {
    abortBtn.classList.toggle("visible", running && disc.can_abort !== false);
    abortBtn.disabled = !(running && disc && disc.can_abort !== false);
  }
  if (wrap) wrap.classList.toggle("discovering", running);
  renderDiscoverPopover(disc);
}

function updateStatusBar(runtime) {
  const bar = document.getElementById("status-bar");
  const text = document.getElementById("status-bar-text");
  const meta = document.getElementById("status-bar-meta");
  if (!bar || !text) return;
  const disc = runtime && runtime.discovery;
  const jobs = (runtime && runtime.running_jobs) || [];
  if (disc && disc.running) {
    bar.classList.add("visible", "discovery");
    text.textContent = disc.phase_label || "Discovering…";
    const collecting = (disc.sources || []).filter(s => s.status === "collecting").length;
    const total = (disc.sources || []).reduce((n, s) => n + (s.count || 0), 0);
    if (meta) meta.textContent = `${total} listings · ${collecting} source${collecting === 1 ? "" : "s"} active`;
    return;
  }
  if (jobs.length) {
    bar.classList.add("visible");
    bar.classList.remove("discovery");
    const j = jobs[0];
    text.textContent = `${j.company || "Job"} — ${statusLabel(j.status)}`;
    if (meta) meta.textContent = jobs.length > 1 ? `+${jobs.length - 1} more` : (j.title || "");
    return;
  }
  bar.classList.remove("visible", "discovery");
}

async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    const json = JSON.stringify(data);
    if (json === lastRuntimeJSON) return;
    lastRuntimeJSON = json;
    discoveryState = data.discovery || null;
    syncDiscoverUI();
    updateStatusBar(data);
  } catch (e) { /* ignore */ }
}

async function loadActivity() {
  if (!selectedId) return;
  try {
    const res = await fetch(`/api/jobs/${selectedId}/activity`);
    const data = await res.json();
    activityEvents = data.events || [];
    renderActivityFeed();
  } catch (e) { /* ignore */ }
}

let lastJobsJSON = null;

function appliedDateKey(job) {
  return Date.parse(job.applied_at || job.updated_at || job.created_at || "") || 0;
}

function formatAppliedAddress(value) {
  if (typeof value === "string") return value.trim();
  if (!value || typeof value !== "object") return "";
  const line1 = value.line1 || value.address_line1 || value.street || "";
  const city = value.city || "";
  const state = value.state || value.region || "";
  const zip = value.zip || value.postal_code || value.postalCode || "";
  if (!line1 && !city && !state && !zip) return "";
  const locality = [city, state].filter(Boolean).join(", ");
  return [line1, [locality, zip].filter(Boolean).join(" ")].filter(Boolean).join(", ");
}

function appliedAddressText(job) {
  const candidates = [
    job.applied_address,
    job.filled_address,
    job.address,
    job.profile_snapshot && job.profile_snapshot.address,
    job.fill_report && job.fill_report.address,
  ];
  for (const candidate of candidates) {
    const formatted = formatAppliedAddress(candidate);
    if (formatted) return formatted;
  }
  return "";
}

function appliedJobs() {
  const items = jobs.filter(j => j.status === "applied");
  const dir = appliedSortDir === "asc" ? 1 : -1;
  const cmpStr = (a, b, field) => dir * (a[field] || "").localeCompare(b[field] || "", undefined, { sensitivity: "base" });
  items.sort((a, b) => {
    switch (appliedSortKey) {
      case "company": return cmpStr(a, b, "company");
      case "title": return cmpStr(a, b, "title");
      case "location": return cmpStr(a, b, "location");
      case "address": return dir * appliedAddressText(a).localeCompare(appliedAddressText(b), undefined, { sensitivity: "base" });
      case "source": return cmpStr(a, b, "source");
      case "date":
      default: return dir * (appliedDateKey(a) - appliedDateKey(b));
    }
  });
  return items;
}

function setAppliedSort(key) {
  if (appliedSortKey === key) {
    appliedSortDir = appliedSortDir === "asc" ? "desc" : "asc";
  } else {
    appliedSortKey = key;
    appliedSortDir = key === "date" ? "desc" : "asc";
  }
  renderDetail();
}

function setAppliedSortFromSelect(value) {
  if (!value || value === appliedSortKey) return;
  appliedSortKey = value;
  appliedSortDir = value === "date" ? "desc" : "asc";
  renderDetail();
}

function toggleAppliedSortDir() {
  appliedSortDir = appliedSortDir === "asc" ? "desc" : "asc";
  renderDetail();
}

function trackingSortHeader(key, label) {
  const sorted = appliedSortKey === key;
  const ind = sorted ? (appliedSortDir === "asc" ? "▲" : "▼") : "";
  return `<th class="sortable${sorted ? " sorted" : ""}" onclick="setAppliedSort('${key}')" title="Sort by ${label}">${label}${ind ? `<span class="sort-ind">${ind}</span>` : ""}</th>`;
}

function trackingLinkCell(url, label) {
  if (!url) return '<span class="cell-muted">—</span>';
  return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${escapeHtml(label)}</a>`;
}

function appliedDateInputValue(job) {
  const raw = job.applied_at || job.updated_at || job.created_at || "";
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toISOString().slice(0, 10);
}

function openAppliedEditor(jobId) {
  editingAppliedId = jobId;
  selectedId = jobId;
  renderDetail();
  requestAnimationFrame(() => document.querySelector("#applied-edit-form input")?.focus());
}

function cancelAppliedEditor() {
  editingAppliedId = null;
  renderDetail();
}

async function saveAppliedJob(jobId) {
  const form = document.getElementById("applied-edit-form");
  if (!form) return;
  const saveButton = form.querySelector('button[type="submit"]');
  if (saveButton) saveButton.disabled = true;
  try {
    const payload = Object.fromEntries(new FormData(form).entries());
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/edit`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save changes");
    editingAppliedId = null;
    await poll();
  } catch (error) {
    alert(error.message || "Could not save changes");
    if (saveButton) saveButton.disabled = false;
  }
}

function renderAppliedEditorHtml(job) {
  const jid = jsStringEscape(job.id);
  const field = (name, label, value, type = "text") => `
    <label>${label}<input type="${type}" name="${name}" value="${escapeHtml(value || "")}"></label>`;
  return `
    <tr class="applied-edit-row">
      <td colspan="9">
        <form id="applied-edit-form" class="applied-edit-form" onsubmit="event.preventDefault(); saveAppliedJob('${jid}')">
          ${field("company", "Company", job.company)}
          ${field("title", "Title", job.title)}
          ${field("location", "Job location", job.location)}
          ${field("applied_address", "Applicant address", appliedAddressText(job))}
          ${field("applied_date", "Date applied", appliedDateInputValue(job), "date")}
          ${field("source", "Source", job.source)}
          ${field("apply_url", "Apply URL", job.apply_url || job.job_url, "url")}
          <label class="edit-notes">Notes / status detail
            <textarea name="status_detail" rows="2">${escapeHtml(job.status_detail || "")}</textarea>
          </label>
          <div class="applied-edit-actions">
            <button type="button" onclick="cancelAppliedEditor()">Cancel</button>
            <button type="submit" class="primary">Save</button>
          </div>
        </form>
      </td>
    </tr>`;
}

function renderAppliedTableHtml() {
  const applied = appliedJobs();
  const countText = applied.length
    ? `${applied.length} applied application${applied.length === 1 ? "" : "s"}`
    : "No applied applications yet";
  const sortOptions = [
    ["date", "Date applied"],
    ["company", "Company"],
    ["title", "Title"],
    ["location", "Location"],
    ["address", "Address"],
    ["source", "Source"],
  ].map(([value, label]) =>
    `<option value="${value}"${appliedSortKey === value ? " selected" : ""}>${label}</option>`
  ).join("");
  const dirLabel = appliedSortDir === "asc" ? "↑ asc" : "↓ desc";

  let body;
  if (!applied.length) {
    body = '<div class="tracking-empty">Mark a job Submitted to see it here.</div>';
  } else {
    const rows = applied.map(job => {
      const href = applicationHref(job);
      const selected = job.id === selectedId ? " selected" : "";
      const address = appliedAddressText(job);
      // Label "Resume" (not the path/id); same /resume/<id> open as before. Path stays in title.
      const resumeCell = job.resume_path
        ? `<a href="/resume/${encodeURIComponent(job.id)}" target="_blank" rel="noopener" title="${escapeHtml(job.resume_path)}" onclick="event.stopPropagation()">Resume</a>`
        : '<span class="cell-muted">—</span>';
      const editRow = editingAppliedId === job.id ? renderAppliedEditorHtml(job) : "";
      return `
      <tr class="tracking-row${selected}" onclick="${escapeAttr(`selectAppliedJob('${jsStringEscape(job.id)}')`)}">
        <td class="cell-edit"><button type="button" class="tiny-edit" title="Edit applied job" aria-label="Edit ${escapeHtml(job.company || job.title || "applied job")}" onclick="${escapeAttr(`event.stopPropagation(); openAppliedEditor('${jsStringEscape(job.id)}')`)}">✎</button></td>
        <td class="cell-company">${escapeHtml(job.company || "—")}</td>
        <td class="cell-title">${escapeHtml(job.title || "—")}</td>
        <td class="cell-muted">${escapeHtml(job.location || "—")}</td>
        <td class="cell-address cell-muted" title="${escapeHtml(address)}">${escapeHtml(address || "—")}</td>
        <td class="cell-muted">${escapeHtml(job.source || "—")}</td>
        <td class="cell-muted">${formatDateFull(job.applied_at || job.updated_at || job.created_at)}</td>
        <td>${trackingLinkCell(href, "Apply")}</td>
        <td>${resumeCell}</td>
      </tr>${editRow}`;
    }).join("");
    body = `
    <div class="tracking-scroll">
      <table class="tracking-table">
        <thead>
          <tr>
            <th class="cell-edit" aria-label="Edit"></th>
            ${trackingSortHeader("company", "Company")}
            ${trackingSortHeader("title", "Title")}
            ${trackingSortHeader("location", "Location")}
            ${trackingSortHeader("address", "Address")}
            ${trackingSortHeader("source", "Source")}
            ${trackingSortHeader("date", "Date applied")}
            <th>Apply URL</th>
            <th>Resume</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  return `
    <div class="applied-tracking">
      <div class="applied-tracking-header">
        <div>
          <h2>Applied applications</h2>
          <div class="applied-tracking-meta">${countText} · click a row for details</div>
        </div>
        <div class="applied-tracking-sort">
          <label>Sort
            <select onchange="setAppliedSortFromSelect(this.value)" aria-label="Sort applied applications">
              ${sortOptions}
            </select>
          </label>
          <button type="button" onclick="toggleAppliedSortDir()" title="Toggle sort direction">${dirLabel}</button>
        </div>
      </div>
      ${body}
    </div>`;
}

function selectAppliedJob(id) {
  scrollToAppliedDetail = true;
  selectJob(id);
}

async function poll() {
  try {
    const res = await fetch("/api/jobs");
    const data = await res.json();
    const hold = !!data.fill_hold_active;
    const newJobsJSON = JSON.stringify(data.jobs || []);
    if (newJobsJSON === lastJobsJSON && hold === fillHoldActive) return;
    fillHoldActive = hold;
    lastJobsJSON = newJobsJSON;
    jobs = data.jobs || [];
    // Keep unsaved inline edits intact if another job changes during polling.
    if (editingAppliedId) return;
    render();
  } catch (e) { console.error("poll failed", e); }
}

// ------------------------------------------------------------ Utility pane

function openUtil(tab) {
  const pane = document.getElementById("console-pane");
  if (!pane) return;
  // Profile is the only util view; clicking Profile again closes it.
  if (tab === "profile" && !pane.classList.contains("hidden")) {
    pane.classList.add("hidden");
    return;
  }
  pane.classList.remove("hidden");
  for (const v of document.querySelectorAll(".util-view")) {
    v.classList.toggle("active", v.id === `view-${tab}`);
  }
  if (tab === "profile") loadProfile();
}

function closeUtil() {
  const pane = document.getElementById("console-pane");
  if (pane) pane.classList.add("hidden");
}

// -------------------------------------------------------------- Profile

async function loadProfile() {
  const res = await fetch("/api/profile");
  const data = await res.json();
  document.getElementById("profile-editor").value = JSON.stringify(data, null, 2);
}

async function saveProfile() {
  const raw = document.getElementById("profile-editor").value;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    alert("Not valid JSON: " + e.message);
    return;
  }
  const res = await fetch("/api/profile", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(parsed),
  });
  if (res.ok) alert("Profile saved."); else alert("Save failed.");
}

// ------------------------------------------------------------------ Cron

let cronJobId = null;
let cronHour = 9;
let cronMinute = 0;

function formatCronClock(hour, minute) {
  const h = ((Number(hour) % 24) + 24) % 24;
  const m = ((Number(minute) % 60) + 60) % 60;
  const ampm = h >= 12 ? "pm" : "am";
  const h12 = h % 12 || 12;
  return m ? `${h12}:${String(m).padStart(2, "0")}${ampm}` : `${h12}${ampm}`;
}

function parseCronExpr(expr) {
  const parts = String(expr || "").trim().split(/\s+/);
  if (parts.length < 2) return { minute: 0, hour: 9 };
  const minute = Number.parseInt(parts[0], 10);
  const hour = Number.parseInt(parts[1], 10);
  if (Number.isNaN(minute) || Number.isNaN(hour)) return { minute: 0, hour: 9 };
  return { minute: Math.min(59, Math.max(0, minute)), hour: Math.min(23, Math.max(0, hour)) };
}

function syncCronUI(enabled) {
  const el = document.getElementById("cron-toggle");
  if (el) {
    el.disabled = enabled == null;
    el.checked = !!enabled;
  }
  const wrapLabel = document.getElementById("cron-toggle-label");
  if (wrapLabel) {
    wrapLabel.classList.toggle("cron-on", enabled === true);
    wrapLabel.classList.toggle("cron-off", enabled === false);
    if (enabled == null) wrapLabel.classList.remove("cron-on", "cron-off");
    wrapLabel.title = enabled == null
      ? "Cron job not found"
      : (enabled
        ? `Daily discovery ON at ${formatCronClock(cronHour, cronMinute)}`
        : "Daily discovery schedule (off)");
  }
  const label = document.getElementById("cron-label");
  if (label) {
    label.textContent = enabled == null ? "Cron unavailable" : (enabled ? "Cron on" : "Cron off");
    label.classList.toggle("cron-on", enabled === true);
    label.classList.toggle("cron-off", enabled === false);
    if (enabled == null) label.classList.remove("cron-on", "cron-off");
  }
  const timeEl = document.getElementById("cron-time");
  if (timeEl) {
    timeEl.disabled = enabled == null;
    timeEl.value =
      `${String(cronHour).padStart(2, "0")}:${String(cronMinute).padStart(2, "0")}`;
  }
  const hint = document.getElementById("cron-hint");
  if (hint) {
    if (enabled == null) {
      hint.textContent = "Cron job not found (job-hunter-daily). Check OpenClaw cron.";
    } else if (enabled) {
      hint.textContent = `Runs job-hunter-daily discovery at ${formatCronClock(cronHour, cronMinute)} local.`;
    } else {
      hint.textContent = "Turn on to schedule daily discovery. Set the time below.";
    }
  }
}

async function loadCron() {
  try {
    const res = await fetch("/api/cron");
    const data = await res.json();
    if (data.error || !data.id) {
      syncCronUI(null);
      return;
    }
    cronJobId = data.id;
    const hm = data.hour != null && data.minute != null
      ? { hour: data.hour, minute: data.minute }
      : parseCronExpr(data.schedule && data.schedule.expr);
    cronHour = hm.hour;
    cronMinute = hm.minute;
    syncCronUI(!!data.enabled);
  } catch (e) { /* ignore */ }
}

async function toggleCron() {
  const el = document.getElementById("cron-toggle");
  const enable = !!(el && el.checked);
  try {
    const res = await fetch("/api/cron/toggle", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enable }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert(data.error || `Could not ${enable ? "enable" : "disable"} cron (${res.status})`);
    }
  } catch (e) {
    alert(String(e));
  }
  await loadCron();
}

async function saveCronSchedule() {
  const timeEl = document.getElementById("cron-time");
  const raw = (timeEl && timeEl.value) || "09:00";
  const [hs, ms] = raw.split(":");
  const hour = Number.parseInt(hs, 10);
  const minute = Number.parseInt(ms || "0", 10);
  if (Number.isNaN(hour) || Number.isNaN(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    alert("Enter a valid time (HH:MM).");
    return;
  }
  const res = await fetch("/api/cron/schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hour, minute, time: raw }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    alert(data.error || `Could not update schedule (${res.status})`);
    return;
  }
  cronHour = hour;
  cronMinute = minute;
  await loadCron();
}

poll();
pollStatus();
loadCron();
syncTestModeToggleUI();
renderDiscoverPopover(null);
setInterval(poll, 3000);
setInterval(pollStatus, 1500);
setInterval(loadCron, 15000);

// ---------------------------------------------------------- UI lifecycle
// Desktop app / browser tab close must stop the dashboard stack. Each tab
// gets a client_id; heartbeats track connected clients. Closing one of many
// tabs only removes that client — last tab sendBeacon / Quit / Cmd+Q shuts
// down. Idle heartbeat stall does NOT auto-quit.
// Refresh → POST /api/restart (cleanup + relaunch server) then reload *this*
// window in place — never window.close(). Quit / header X / last-window close
// → POST /api/shutdown (no restart flag); launcher then kills dedicated
// dashboard Chrome and exits the Dock applet. Heartbeat timeout is backup.
const UI_CLIENT_STORAGE_KEY = "jobHunterDashboardClientId";
const UI_HEARTBEAT_MS = 5000;
let _dashboardRestartInFlight = false;
let _dashboardQuitInFlight = false;

const REFRESH_BTN_ICON_HTML = `
  <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
    <path fill="currentColor" d="M13.65 2.35A7.96 7.96 0 0 0 8 0C3.58 0 0 3.58 0 8s3.58 8 8 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 8 14A6 6 0 1 1 8 2c1.66 0 3.14.69 4.22 1.78L9 7h7V0l-2.35 2.35z"/>
  </svg>`.trim();

function dashboardClientId() {
  let id = sessionStorage.getItem(UI_CLIENT_STORAGE_KEY);
  if (!id) {
    id = (crypto.randomUUID && crypto.randomUUID())
      || `tab-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    sessionStorage.setItem(UI_CLIENT_STORAGE_KEY, id);
  }
  return id;
}

async function sendUiHeartbeat() {
  if (_dashboardRestartInFlight || _dashboardQuitInFlight) return;
  try {
    await fetch("/api/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: dashboardClientId() }),
      keepalive: true,
    });
  } catch (e) { /* server may already be stopping */ }
}

function beaconUiShutdown() {
  // Refresh owns cleanup via /api/restart; Quit already POSTed /api/shutdown.
  if (_dashboardRestartInFlight || _dashboardQuitInFlight) return;
  const body = JSON.stringify({ client_id: dashboardClientId() });
  try {
    if (navigator.sendBeacon) {
      navigator.sendBeacon(
        "/api/shutdown",
        new Blob([body], { type: "application/json" }),
      );
      return;
    }
  } catch (e) { /* fall through */ }
  try {
    fetch("/api/shutdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    });
  } catch (e) { /* ignore */ }
}

async function restartDashboard() {
  if (_dashboardRestartInFlight || _dashboardQuitInFlight) return;
  _dashboardRestartInFlight = true;
  const btn = document.getElementById("refresh-btn");
  const quitBtn = document.getElementById("quit-btn");
  if (btn) btn.disabled = true;
  if (quitBtn) quitBtn.disabled = true;
  try {
    await fetch("/api/restart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: dashboardClientId() }),
      keepalive: true,
    });
  } catch (e) { /* server dying mid-response is expected */ }
  // Keep this window open. Launcher respawns server only; we reload in place.
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 500));
    try {
      const res = await fetch("/api/status", { cache: "no-store" });
      if (res.ok) {
        window.location.reload();
        return;
      }
    } catch (e) { /* still down */ }
  }
  if (btn) {
    btn.disabled = false;
    btn.innerHTML = REFRESH_BTN_ICON_HTML;
  }
  if (quitBtn) quitBtn.disabled = false;
  _dashboardRestartInFlight = false;
  alert("Dashboard did not come back within ~30s. Check logs/dashboard_server.out.");
}

async function quitDashboard() {
  if (_dashboardQuitInFlight || _dashboardRestartInFlight) return;
  _dashboardQuitInFlight = true;
  const btn = document.getElementById("quit-btn");
  const refreshBtn = document.getElementById("refresh-btn");
  if (btn) btn.disabled = true;
  if (refreshBtn) refreshBtn.disabled = true;
  // force=true: kill stack even if other tabs are still heartbeating (explicit Quit).
  // No restart flag — launcher exits after server stops.
  try {
    await fetch("/api/shutdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: dashboardClientId(), force: true }),
      keepalive: true,
    });
  } catch (e) { /* server dying mid-response is expected */ }
  try { window.close(); } catch (e) { /* ignore */ }
}

sendUiHeartbeat();
setInterval(sendUiHeartbeat, UI_HEARTBEAT_MS);
window.addEventListener("pagehide", beaconUiShutdown);
window.addEventListener("beforeunload", beaconUiShutdown);
// unload as extra belt for Cmd+Q / hard quit when pagehide alone is flaky.
window.addEventListener("unload", beaconUiShutdown);

// Click/tap toggles the discovery popover (hover still works on desktop).
document.getElementById("discover-wrap")?.addEventListener("click", (e) => {
  const wrap = document.getElementById("discover-wrap");
  if (!wrap) return;
  if (e.target.closest("#discover-abort-btn")) return;
  if (e.target.closest("input, label.discover-src-opt, .discover-popover button")) return;
  if (e.target.closest("#discover-btn") && !(discoveryState && discoveryState.running)) return;
  wrap.classList.toggle("open");
});
document.addEventListener("click", (e) => {
  const wrap = document.getElementById("discover-wrap");
  if (wrap && !wrap.contains(e.target)) wrap.classList.remove("open");
  const cronWrap = document.getElementById("cron-wrap");
  if (cronWrap && !cronWrap.contains(e.target)) cronWrap.classList.remove("open");
});
// Click/tap toggles the cron popover (hover still works on desktop).
document.getElementById("cron-wrap")?.addEventListener("click", (e) => {
  const wrap = document.getElementById("cron-wrap");
  if (!wrap) return;
  // Checkbox / time / save handle themselves; other clicks pin the popover.
  if (e.target.closest("input, button, label.cron-toggle, label.cron-time-row")) return;
  wrap.classList.toggle("open");
});
// Checkbox focus keeps :focus-within popovers open after mouseleave — blur on leave
// unless the wrap is explicitly pinned (.open).
["test-mode-wrap", "cron-wrap", "discover-wrap", "filter-row", "add-job-wrap"].forEach((id) => {
  const wrap = document.getElementById(id);
  if (!wrap) return;
  wrap.addEventListener("mouseleave", () => {
    if (wrap.classList.contains("open")) return;
    const ae = document.activeElement;
    if (ae && wrap.contains(ae) && typeof ae.blur === "function") ae.blur();
  });
});
// Live activity: poll only while a run is in progress.
setInterval(() => {
  if (!selectedId) return;
  const job = jobs.find(j => j.id === selectedId);
  if (!job) return;
  const active = (
    job.status === "filling"
    || job.status === "tailoring"
    || job.status === "navigating"
    || job.status === "resuming"
  );
  if (!active) return;
  loadActivity();
  setTimeout(() => { if (selectedId === job.id) loadActivity(); }, 1200);
}, 2000);
