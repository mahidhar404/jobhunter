const STAGES = ["discovered", "tailoring", "navigating", "filling", "ready_for_review", "applied"];
// temporary UI override — remove when user says undo
const TEMP_APPLIED_COUNT_OVERRIDE = null;
const STATUS_COLORS = {
  discovered: "#7a828c",
  tailoring: "#e8913a",
  navigating: "#e8913a",
  filling: "#e8913a",
  resuming: "#e8913a",
  resume_ready: "#5cb8a8",
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
  staffing: { short: "Staffing agency", long: "Staffing / recruiting agency" },
  stale_listing: { short: "Stale listing", long: "Listing posted more than 10 days ago" },
  user: { short: "Skipped / deleted by you", long: "Skipped or deleted by you" },
  manual: { short: "Skipped / deleted by you", long: "Skipped or deleted by you" },
  skipped_manual: { short: "Skipped by you", long: "Skipped by you" },
  duplicate: { short: "Duplicate (merged)", long: "Duplicate — merged into another listing" },
  contract: { short: "Contract / C2C", long: "Contract / C2C" },
  easy_apply: { short: "Easy apply", long: "Easy apply / aggregator apply" },
  unresolved_apply_url: { short: "Unresolved URL", long: "Apply URL still LinkedIn / aggregator after resolve" },
  apply_resolve_failed: { short: "Unresolved URL", long: "Apply URL still LinkedIn / aggregator after resolve" },
  // Concrete dead-link reasons (link_liveness) — prefer these over vague generics.
  "dead/404": { short: "dead/404", long: "Apply/listing URL HTTP 404 — posting not found" },
  "dead/410": { short: "dead/410", long: "Apply/listing URL HTTP 410 — posting gone" },
  "closed/lever": { short: "closed/lever", long: "Lever closed posting (couldn't find / posting closed)" },
  "closed/greenhouse": { short: "closed/greenhouse", long: "Greenhouse job posting no longer available" },
  "closed/workday": { short: "closed/workday", long: "Workday job no longer available / not found" },
  "closed/ashby": { short: "closed/ashby", long: "Ashby job posting no longer available" },
  "closed/smartrecruiters": { short: "closed/smartrecruiters", long: "SmartRecruiters job posting closed / not found" },
  "closed/workable": { short: "closed/workable", long: "Workable job posting closed / not found" },
  "closed/bamboo": { short: "closed/bamboo", long: "BambooHR job posting closed / not found" },
  "closed/jazzhr": { short: "closed/jazzhr", long: "JazzHR / applytojob posting closed / not found" },
  "closed/pinpoint": { short: "closed/pinpoint", long: "Pinpoint job posting closed / not found" },
  "closed/ats": { short: "closed/ats", long: "ATS posting closed / no longer available" },
  closed_posting: { short: "closed posting", long: "Apply URL closed or dead (see status detail)" },
  dead_apply_url: { short: "dead/404", long: "Apply URL closed or dead" },
  dead_link: { short: "Dead link", long: "Dead or broken link" },
};
/** Canonical order for Deleted reason groups (empty key = No reason, last). */
const DELETED_REASON_ORDER = [
  "excessive_yoe",
  "citizenship_or_greencard",
  "clearance_or_intel",
  "management_track",
  "non_us_location",
  "staffing",
  "stale_listing",
  "contract",
  "easy_apply",
  "unresolved_apply_url",
  "duplicate",
  "dead/404",
  "dead/410",
  "closed/lever",
  "closed/greenhouse",
  "closed/workday",
  "closed/ashby",
  "closed/smartrecruiters",
  "closed/workable",
  "closed/bamboo",
  "closed/jazzhr",
  "closed/pinpoint",
  "closed/ats",
  "closed_posting",
  "dead_link",
  "skipped_manual",
  "user",
  "",
];
const TERMINAL = ["applied"];
// Most urgent first - used to pick the single indicator dot a collapsed
// company group shows, and to decide which groups float to the top of
// the list (see groupPriorityStatus/render()).
const PRIORITY_ORDER = [
  "stuck", "blocked_captcha", "filling", "navigating", "tailoring", "resuming",
  "resume_ready", "ready_for_review", "discovered", "applied",
];
const ACTIVE_PROGRESS_STATUSES = new Set(["tailoring", "navigating", "filling", "resuming"]);
const IN_PROGRESS_OR_NEEDS_ATTENTION = [
  "tailoring", "navigating", "filling", "resuming", "resume_ready",
  "stuck", "blocked_captcha",
];
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
const SENIORITY_EXCLUDE_RE = /\b(principal|(?<!technical\s)staff|lead|manager|mgr|director|vp|svp|evp|vice[\s-]+president|head\s+of|chief|founder|partner|fellow|distinguished|supervisor|architect|cto|ceo|cpo|cfo|coo|cio)\b/i;
const NON_US_LOCATION_RE = /\b(india|japan|china|singapore|philippines|germany|france|poland|mexico|brazil|australia|vietnam|indonesia|malaysia|thailand|canada|united\s+kingdom|\buk\b|england|scotland|ireland|wales|netherlands|spain|italy|sweden|norway|denmark|switzerland|belgium|portugal|austria|finland|israel|south\s+korea|\bkorea\b|taiwan|hong\s+kong|dubai|u\.?a\.?e\.?|united\s+arab\s+emirates|new\s+zealand|argentina|colombia|chile|peru|ecuador|bolivia|uruguay|paraguay|venezuela|guatemala|honduras|nicaragua|costa\s+rica|panama|dominican\s+republic|saudi\s+arabia|\bksa\b|qatar|kuwait|bahrain|oman|jordan|lebanon|egypt|morocco|tunisia|nigeria|kenya|ghana|ethiopia|south\s+africa|ukraine|romania|serbia|slovakia|slovenia|croatia|hungary|czech(\s+republic)?|\bczechia\b|bulgaria|lithuania|latvia|estonia|greece|turkey|turkiye|pakistan|bangladesh|sri\s+lanka|nepal|cambodia|myanmar|armenia|azerbaijan|kazakhstan|uzbekistan|tajikistan|north\s+macedonia|macedonia|belarus|moldova|europe|european(\s+union)?|emea|apac|latam|\basia\b|africa|middle\s+east|worldwide|\bglobal\b|ontario|quebec|alberta|manitoba|saskatchewan|british\s+columbia|nova\s+scotia|new\s+brunswick|newfoundland|prince\s+edward|karnataka|telangana|maharashtra|tamil\s+nadu|kerala|gujarat|haryana|uttar\s+pradesh|west\s+bengal|andhra\s+pradesh|rajasthan|madhya\s+pradesh|odisha|assam|jharkhand|bangalore|bengaluru|mumbai|delhi|hyderabad|pune|chennai|kolkata|gurgaon|gurugram|noida|ahmedabad|jaipur|coimbatore|kochi|thiruvananthapuram|trivandrum|indore|bhubaneswar|vadodara|nagpur|mysuru|visakhapatnam|lucknow|chandigarh|kuala\s+lumpur|penang|bangkok|hanoi|ho\s+chi\s+minh|istanbul|athens|zagreb|gdansk|wroclaw|tokyo|osaka|shanghai|beijing|shenzhen|manila|jakarta|toronto|vancouver|montreal|ottawa|calgary|edmonton|kitchener|kitchener-waterloo|mississauga|winnipeg|halifax|london|paris|munich|berlin|amsterdam|dublin|zurich|geneva|stockholm|copenhagen|oslo|helsinki|lisbon|madrid|barcelona|rome|milan|prague|budapest|vienna|brussels|warsaw|krakow|bucharest|sofia|belgrade|bratislava|vilnius|tallinn|riga|edinburgh|glasgow|melbourne|sydney|brisbane|perth|adelaide|auckland|wellington|seoul|taipei|tel\s+aviv|jerusalem|haifa|sao\s+paulo|rio\s+de\s+janeiro|bogota|medellin|santiago|buenos\s+aires|lima|quito|montevideo|mexico\s+city|ciudad\s+de\s+mexico|guadalajara|monterrey|dubai|abu\s+dhabi|doha|riyadh|jeddah|cape\s+town|johannesburg|lagos|nairobi|almaty|astana|nur-sultan|san\s+salvador|stuttgart|frankfurt|hamburg|cologne|dusseldorf|lyon|marseille|toulouse|lille|\bgbr\b|\bcan\b|\bind\b|\baus\b|\bdeu\b|\bfra\b|\bnld\b|\bsgp\b|\birl\b|\bnzl\b|\bpol\b|\bmex\b|\bbra\b|\besp\b|\bita\b|\bswe\b|\bnor\b|\bdnk\b|\bche\b|\bbel\b|\bprt\b|\baut\b|\bfin\b|\bisr\b|\bkor\b|\btwn\b|\bphl\b|\bare\b|\brou\b|\buae\b|\bsau\b|\bqat\b)\b/i;
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
  "jp", "ke", "kr", "kw", "kz", "lk", "lt", "lu", "lv", "ma", "mg", "mx", "my",
  "ng", "nl", "no", "nz", "pa", "pe", "ph", "pk", "pl", "pt", "qa", "ro", "rs",
  "ru", "sa", "se", "sg", "si", "sk", "sv", "th", "tr", "tw", "ua", "uk", "uy",
  "ve", "vn", "za",
]);
// India region heuristics — keep in sync with scripts/discovery_filters.py
// (INDIA_LOCATION_RE / INDIA_REMOTE_RE). Used for the region-aware hide of
// untouched discovered jobs and the Ops Region filter.
const INDIA_LOCATION_RE = /\b(india|bharat|karnataka|telangana|maharashtra|tamil\s+nadu|kerala|gujarat|haryana|uttar\s+pradesh|west\s+bengal|andhra\s+pradesh|rajasthan|madhya\s+pradesh|odisha|assam|jharkhand|punjab|bihar|chhattisgarh|uttarakhand|goa|bangalore|bengaluru|mumbai|bombay|delhi|new\s+delhi|hyderabad|pune|chennai|madras|kolkata|calcutta|gurgaon|gurugram|noida|ghaziabad|ahmedabad|jaipur|coimbatore|kochi|cochin|thiruvananthapuram|trivandrum|indore|bhubaneswar|vadodara|nagpur|mysuru|mysore|visakhapatnam|vizag|lucknow|chandigarh|surat|nashik|thane|\bncr\b)\b/i;
const INDIA_REMOTE_RE = /(remote[,\s/\-]*india|india[,\s/\-]*remote|india\s*\(\s*remote\s*\)|remote\s*\(\s*india\s*\)|(?:wfh|work\s+from\s+home)[,\s/\-]*india|anywhere\s+in\s+india|pan[\s\-]*india|across\s+india)/i;
const GEORGIA_COUNTRY_CITY_RE = /\b(?:tbilisi|batumi)\b/i;
// Clearance requirement language — not bare "security" / "secret" alone.
const CLEARANCE_REQUIREMENT_RE = /(\bts[\s_\/.\-]*sci\b|\btop[\s\-]*secret\b|(?<!employee\s)\bpolygraph\b|\b(?:ci|full[\s\-]*scope)[\s\-]*poly(?:graph)?\b|\b(?:q|l)[\s\-]*clearance\b|\bdoe[\s\-]*(?:q|l)\b|\bdod[\s\-]*(?:secret|top[\s\-]*secret|ts|clearance)\b|\bsecret[\s\-]*clearance\b|\bsecurity[\s\-]*clearance\b|\bactive[\s\-]*(?:ts|sci|secret|top[\s\-]*secret|security)?[\s\-]*clearance\b|\b(?:ts|secret|top[\s\-]*secret)[\s\-]*cleared\b|\bcleared[\s\-]*(?:candidate|personnel|position|role|engineer|scientist)\b|\bclearance[\s\-]*(?:required|mandatory|needed|necessary|eligibility|level|requirements?)\b|\b(?:must|require[ds]?|required|need(?:s|ed)?|possess(?:es|ing)?|hold(?:s|ing)?|obtain(?:able|ing)?|eligible\s+for|ability\s+to\s+obtain|able\s+to\s+obtain|currently\s+(?:hold|have)|have\s+an?\s+active).{0,48}clearance\b|\bclearance(?![\s:\-|*]*\bnot\b).{0,24}(?:required|mandatory|needed)\b|\bclassified\s+(?:information|environment|program|material|data|systems?|networks?|work|facility|facilities)\b|\b(?:handle|access|process|work\s+(?:with|on))\s+classified\b|\bsci[\s\-]*clearance\b|\bsap(?:\/sar)?\s+clearance\b|\bclearance\s*:\s*(?:secret|top[\s\-]*secret|ts(?:[\s_\/.\-]*sci)?|sci|public\s+trust|(?:doe[\s\-]*)?[ql]|active)\b|\bclearance\s*:.{0,48}(?:obtain|eligible|public\s+trust|secret|ts[\s_\/.\-]*sci|polygraph)\b|\bclearance[\s\-]*(?:type|level)\s*:\s*(?:secret|top[\s\-]*secret|ts(?:[\s_\/.\-]*sci)?|sci|public\s+trust|(?:doe[\s\-]*)?[ql]|active|confidential)\b|\bclearance(?:[\s\-]*(?:required(?:\s+for\s+start)?|type|level))?\s*:?\s*(?:\u2026|\.\.\.)\s*\[\s*full\s+text\b|\(\s*public\s+trust\s*\)|\bpublic\s+trust\s+clearance\b|\b(?:must|require[ds]?|required|need(?:s|ed)?|possess(?:es|ing)?|hold(?:s|ing)?|obtain(?:able|ing)?|eligible\s+for|ability\s+to\s+obtain|able\s+to\s+obtain|currently\s+(?:hold|have)|have\s+an?\s+active|maintain(?:ing)?).{0,48}public\s+trust\b|\bpublic\s+trust(?:\s+clearance)?[\s\-]*(?:required|mandatory|needed)\b)/i;
// ATS "Clearance required: No/None" / "Clearance Not Required" — stripped first.
const CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE = /(\bclearance[\s\-]*(?:required|preferred|mandatory|needed)(?:\s+for\s+start)?[\s:\-|*]*(?:no|none|n\/?a)\b|\bno\s+(?:security\s+)?clearance(?:\s+is)?\s+required\b|\bdoes\s+not\s+require\s+(?:an?\s+)?(?:security\s+)?clearance\b|\b(?:an?\s+)?(?:security\s+)?clearance\s+is\s+not\s+required\b|\b(?:(?:an?\s+|the\s+)?(?:security\s+)?)?clearance[\s:\-|*]*not[\s\-]+(?:required|needed|mandatory|necessary)\b)/i;
const CLEARANCE_PREFERRED_ONLY_RE = /(\b(?:an?\s+)?(?:active\s+)?(?:secret|top[\s\-]*secret|ts(?:[\s_\/.\-]*sci)?|security)?[\s\-]*clearance\s+is\s+preferred\b|\b(?:active\s+)?(?:secret|top[\s\-]*secret|security)[\s\-]*clearance\s+preferred\b|\bclearance[\s\-]*preferred\b|\bpreferred[\s:]+(?:an?\s+)?(?:active\s+)?(?:secret|top[\s\-]*secret|ts|security)?[\s\-]*clearance\b|\bsecurity[\s\-]*clearance\s+verification\b|\bclearance\s+verification\b)/gi;
const INTEL_AGENCY_COMPANY_RE = /(national\s+security\s+agency|\bnsa\b|central\s+intelligence(?:\s+agency)?|\bcia\b|defense\s+intelligence(?:\s+agency)?|\bdia\b|national\s+geospatial(?:[\s\-]+intelligence)?(?:\s+agency)?|\bnga\b|national\s+reconnaissance\s+office|\bnro\b|office\s+of\s+the\s+director\s+of\s+national\s+intelligence|\bodni\b|national\s+counterterrorism\s+center|\bnctc\b|defense\s+counterintelligence\s+and\s+security\s+agency|\bdcsa\b|intelligence\s+community\s+agency|u\.?s\.?\s+intelligence\s+community|\bic\s+agency\b)/i;
const INTEL_AGENCY_URL_RE = /(intelligencecareers\.gov|(?:^|[\.\/])nsa\.gov|(?:^|[\.\/])cia\.gov|(?:^|[\.\/])dia\.mil|(?:^|[\.\/])nga\.mil|(?:^|[\.\/])nro\.gov|(?:^|[\.\/])dni\.gov|(?:^|[\.\/])dcsa\.mil)/i;
const STALE_LISTING_MAX_AGE_DAYS = 10;

// Keep in sync with scripts/discovery_filters.py — YOE / citizenship / work mode.
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
const YOE_TENURE_BEFORE_RE = /(?:(?:more|over)\s+than|(?:nearly|almost|approximately|around|about)|(?:for|with)\s+(?:over|more\s+than)|(?:founded|established|celebrating)|(?:company|holding|firm|business|organization|leader|provider)(?:\s+\w+){0,4}\s+with|(?:our\s+team\s+has|we\s+have)|(?:preferred\s+qualifications?|nice\s+to\s+have)\s*:)\s*$/i;
const YOE_SOFT_BEFORE_RE = /(?:~|\b(?:preferred|desired|ideally|optional|bonus)\b|\bideal(?:ly)?(?:\s+candidate)?\b|\bnice\s+to\s+have\b|\ba\s+plus\b|\bexcited\s+if\s+you\s+have\b|\bwe(?:['’]re|\s+are)\s+excited\s+if\b)[\s\S]{0,100}$/i;
const YOE_SOFT_AFTER_RE = /^\s*(?:is\s+)?(?:preferred|desired|a\s+plus|nice\s+to\s+have|bonus)\b/i;
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
const CITIZENSHIP_OR_GC_REQUIREMENT_RE = /(\b(?:u\.?s\.?|us|united\s+states)\s+citizens?\s+only\b|\bonly\s+(?:u\.?s\.?|us|united\s+states)\s+citizens?\b|\b(?:u\.?s\.?|us|united\s+states)\s+citizenship\s+required\b|\bmust\s+be\s+(?:a\s+)?(?:u\.?s\.?|us|united\s+states)\s+citizen\b|\brequire[sd]?\s+(?:u\.?s\.?|us|united\s+states)\s+citizenship\b|\bcitizenship\s*(?:requirement|:)\s*(?:u\.?s\.?|us|united\s+states)\b|\bgreen\s*card\s+required\b|\bmust\s+(?:have|hold|possess)\s+(?:a\s+)?green\s*card\b|\brequire[sd]?\s+(?:a\s+)?green\s*card\b|\bmust\s+be\s+(?:a\s+)?(?:permanent\s+resident|lawful\s+permanent\s+resident)\b|\b(?:permanent\s+resident|lawful\s+permanent\s+resident)\s+(?:status\s+)?required\b|\bonly\s+(?:u\.?s\.?|us)\s+(?:citizens?|permanent\s+residents?)\b|\bgreen\s*card\s+holders?\s+only\b|\bonly\s+green\s*card\s+holders?\b|\bgreen\s*cards?\s+only\b|\b(?:u\.?s\.?|us|united\s+states)\s+citizens?\s+(?:and|or)\s+(?:green\s*card(?:\s+holders?)?|permanent\s+residents?|gc)\s+only\b|\busc\s*(?:and|&|\/)\s*gc(?:\s+only)?\b(?!\s+(?:is\s+)?preferred)|\bgc\s*(?:and|&|\/)\s*usc(?:\s+only)?\b(?!\s+(?:is\s+)?preferred)|\busc\s*\/\s*gc\b(?!\s+(?:is\s+)?preferred)|\bgc\s*\/\s*usc\b(?!\s+(?:is\s+)?preferred)|\bvisa\s*:\s*usc\b(?!\s+(?:and\s+gc\s+)?(?:is\s+)?preferred))/i;
const NO_VISA_SPONSORSHIP_RE = /(\bno\s+(?:visa\s+|h-?1b\s+|immigration\s+)?sponsorship\b|\bwithout\s+(?:(?:the\s+)?(?:need\s+for\s+)?)?(?:employer\s+|company\s+)?(?:visa\s+|h-?1b\s+|immigration\s+)?sponsorship\b|\b(?:does|do|will|can)\s+not\s+sponsor\b|\bunable\s+to\s+sponsor\b|\bcannot\s+sponsor\b|\bnot\s+(?:able|willing)\s+to\s+sponsor\b|\bno\s+(?:visa\s+)?sponsor(?:ship)?\s+(?:available|provided|offered)\b|\bsponsorship\s+(?:is\s+)?(?:not\s+available|unavailable)\b)/i;
const SPONSORS_VISA_RE = /(\bwe\s+(?:do\s+)?sponsor(?:s)?\s+(?:h-?1b|visas?|work\s+visas?)\b|\b(?:company|employer)\s+sponsors?\s+(?:h-?1b|visas?)\b|\b(?:visa|h-?1b|immigration)\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered|ok|okay)\b|\b(?:will|can|may)\s+sponsor\s+(?:h-?1b|visas?|work\s+visas?)\b|\bsponsorship\s+(?:is\s+)?(?:available|provided|offered)\b|\bopen\s+to\s+(?:visa|h-?1b|immigration)\s+sponsorship\b|\bprovides?\s+(?:visa|h-?1b)\s+sponsorship\b)/i;
const US_PERSON_PREFERRED_ONLY_RE = /(\b(?:u\.?s\.?|us)\s+persons?(?:\s+status)?\s+(?:is\s+)?preferred\b|\bpreferred[\s:]+(?:a\s+)?(?:u\.?s\.?|us)\s+person(?:\s+status)?\b)/gi;
const US_PERSON_IF_EXPORT_BOILERPLATE_RE = /\bif\s+access\s+to\s+export[\s\-]?controlled\b[\s\S]{0,280}?\bis\s+required\b/gi;
const US_PERSON_REQUIRED_RE = /(\b(?:u\.?s\.?|us)\s+persons?(?:\s+status)?\s+(?:is\s+)?required\b|\brequire[sd]?\s+(?:a\s+)?(?:u\.?s\.?|us)\s+person(?:\s+status)?\b|\bmust\s+be\s+(?:a\s+)?(?:u\.?s\.?|us)\s+person\b|\bonly\s+(?:u\.?s\.?|us)\s+persons?\b|\b(?:u\.?s\.?|us)\s+persons?\s+only\b|\bitar\s+requirements?\b|\bitar[\s\-]controlled\b|\bsubject\s+to\s+itar\b|\bitar\b.{0,48}(?:required|restricted|compliance)\b|\b(?:requires?|requiring|needs?|needing|must\s+have)\s+access\s+to\s+(?:u\.?s\.?\s+)?export[\s\-]?controlled\b|\baccess(?:es|ing)?(?:\s+to)?\s+export[\s\-]?controlled\s+(?:data|information|items?|material|technology|source)\b)/i;
/** Visibility-only visa/work-auth phrases (does not prune). */
const JD_VISA_VISIBILITY_RE = /(\b(?:h-?1-?b|h1b)s?\b|\b(?:stem\s+)?opt\b|\bcpt\b|\bead\b|\btn(?:\s+visa)?\b|\bl-?1(?:[ab])?\b|\bo-?1\b|\busc\b|\bgc\b|\bgreen\s*cards?\b|\b(?:u\.?s\.?|us|united\s+states)\s+citizens?\b|\b(?:u\.?s\.?|us)\s+persons?\b|\bitar\b|\bexport[\s\-]?controlled\b|\btop[\s\-]*secret\b|\bts[\s_\/.\-]*sci\b|\bcitizens?\s+only\b|\bcitizenship\b|\bvisas?\b|\b(?:visa\s+|h-?1b\s+|immigration\s+)?sponsorship\b|\bsponsors?(?:\s+(?:h-?1b|visas?|work\s+visas?))?\b|\bwork[\s-]?auth(?:orization)?\b|\bwork[\s-]?visas?\b|\bpermanent\s+residents?\b)/i;
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

// Shared scanner for the strict + fallback salary extractors. Each spec runs
// one regex over `blob` and, for every match that survives the shared
// hourly/funding-noise filters, builds a {min,max,period} pair via
// `amounts(m)` → salaryPairFromAmounts. Per-spec knobs preserve the small
// behavioral differences between the two extractors:
//   - gate(m): extra guard run *before* the noise filters (strict range only)
//   - skipInSpan: skip matches that fall inside an already-recorded range span
//   - trackSpan: "always" | "ifMax" — record this match's span (used to
//     suppress overlapping single-dollar matches after ranges).
// Candidate ordering is preserved, so the ranged-first result is identical.
function extractSalaryScan(blob, specs) {
  if (!blob.trim()) return null;
  const candidates = [];
  const rangeSpans = [];
  let m;
  for (const { re, amounts, gate, skipInSpan, trackSpan } of specs) {
    re.lastIndex = 0;
    while ((m = re.exec(blob)) !== null) {
      const start = m.index;
      const end = m.index + m[0].length;
      if (skipInSpan && rangeSpans.some(([rs, rEnd]) => rs <= start && start < rEnd)) continue;
      if (gate && !gate(m)) continue;
      if (salaryIsHourly(blob, start, end)) continue;
      if (salaryIsFundingNoise(blob, start, end)) continue;
      const [aRaw, bRaw] = amounts(m);
      const pair = salaryPairFromAmounts(aRaw, bRaw);
      if (pair) {
        candidates.push(pair);
        if (trackSpan === "always" || (trackSpan === "ifMax" && bRaw)) {
          rangeSpans.push([start, end]);
        }
      }
    }
  }
  if (!candidates.length) return null;
  const ranged = candidates.filter(c => c.max != null);
  return ranged.length ? ranged[0] : candidates[0];
}

function extractSalary(text, title, description) {
  return extractSalaryScan(salaryBlob(text, title, description), [
    { re: SALARY_LABEL_RE, amounts: (m) => [m[1], m[2]], trackSpan: "ifMax" },
    {
      re: SALARY_RANGE_RE,
      amounts: (m) => [m[1], m[2]],
      trackSpan: "always",
      gate: (m) => {
        const aRaw = m[1];
        const bRaw = m[2];
        const hasCur = /(?:\$|USD)/i.test(aRaw) || /(?:\$|USD)/i.test(bRaw);
        const bothKOrPlain = /(?:[kK]|\d{5,7})/.test(aRaw) && /(?:[kK]|\d{5,7})/.test(bRaw);
        return hasCur || bothKOrPlain;
      },
    },
    { re: SALARY_DOLLAR_SINGLE_RE, amounts: (m) => [m[1], null], skipInSpan: true },
  ]);
}

function extractSalaryFallback(text, title, description) {
  if (extractSalary(text, title, description) != null) return null;
  return extractSalaryScan(salaryBlob(text, title, description), [
    { re: SALARY_FALLBACK_NEAR_KW_RE, amounts: (m) => [m[1] || m[3], m[2] || m[4]] },
    { re: SALARY_FALLBACK_UP_TO_RE, amounts: (m) => [m[1], null] },
    { re: SALARY_FALLBACK_FROM_RE, amounts: (m) => [m[1], null] },
    { re: SALARY_FALLBACK_BARE_K_RANGE_RE, amounts: (m) => [m[1], m[2]] },
  ]);
}

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
  if (GEORGIA_COUNTRY_CITY_RE.test(loc)
      && !/\b(?:GA|USA|US|U\.S\.A?\.?|United\s+States)\b/i.test(loc)) return true;
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
  if (isIndiaLocation(loc) && !US_LOCATION_STRONG_RE.test(loc)) return true;
  if (US_LOCATION_RE.test(loc)) return false;
  return NON_US_LOCATION_RE.test(loc);
}

// True when the location clearly indicates India or remote-India. Mirrors
// scripts/discovery_filters.py is_india_location.
function isIndiaLocation(location) {
  const loc = foldAccents(location || "").trim();
  if (!loc) return false;
  const parts = loc.split(",").map((p) => p.trim());
  const tail = parts.length >= 2 && /^[A-Za-z]{2}$/.test(parts[parts.length - 1])
    ? parts[parts.length - 1]
    : null;
  if (tail && tail.toLowerCase() === "in") {
    const head = parts.slice(0, -1).join(", ");
    const decisive = tail === "in" && (
      parts.length >= 3
      || NON_US_LOCATION_RE.test(head)
      || INDIA_LOCATION_RE.test(head)
    );
    if (decisive && !US_LOCATION_STRONG_RE.test(head)) return true;
  }
  if (INDIA_REMOTE_RE.test(loc)) return true;
  return INDIA_LOCATION_RE.test(loc);
}

// True when a location is kept under the enabled lanes (india / worldwide).
// Mirrors scripts/discovery_filters.py listing_matches_lanes (US onsite/hybrid
// needs work_mode — stamped jobs use job.lane when present).
function isUsBasedLocation(location) {
  if (isIndiaLocation(location)) return false;
  if (isClearlyNonUsLocation(location)) return false;
  const loc = foldAccents(location || "").trim();
  if (!loc) return false;
  return US_LOCATION_RE.test(loc);
}

function locationMatchesRegions(location, regions, workMode) {
  const regs = Array.isArray(regions) ? regions : ["india", "worldwide"];
  if (!regs.length) return false;
  // Map legacy "us" → worldwide
  const lanes = regs.map((r) => (r === "us" ? "worldwide" : r));
  const wm = (workMode || "").toLowerCase();
  if (isUsBasedLocation(location) && (wm === "onsite" || wm === "hybrid")) {
    return false;
  }
  if (isIndiaLocation(location)) return lanes.includes("india");
  return lanes.includes("worldwide");
}

// Best-effort lane tag: "india" | "worldwide" | "unknown".
function regionForLocation(location, workMode) {
  if (isIndiaLocation(location)) return "india";
  const wm = (workMode || "").toLowerCase();
  if (isUsBasedLocation(location) && (wm === "onsite" || wm === "hybrid")) {
    return "unknown";
  }
  return "worldwide";
}

function laneForJob(job) {
  if (!job) return "unknown";
  const stamped = (job.lane || job.region || "").trim();
  if (stamped === "india" || stamped === "worldwide") return stamped;
  if (stamped === "us") return "worldwide";
  return regionForLocation(job.location, job.work_mode);
}

// Enabled discovery lanes. Populated from discovery settings.
let enabledRegions = ["india", "worldwide"];
function getEnabledRegions() {
  return Array.isArray(enabledRegions) && enabledRegions.length
    ? enabledRegions
    : ["india", "worldwide"];
}

// Sync enabledRegions from discovery payload (discover_worldwide / discover_india).
function updateEnabledRegionsFromDiscovery(disc) {
  let ww = true;
  if (disc) {
    if (disc.discover_worldwide !== undefined) ww = disc.discover_worldwide === true;
    else if (disc.discover_us !== undefined) ww = disc.discover_us === true;
  }
  const india = disc ? disc.discover_india !== false : true;
  const regs = [];
  if (india) regs.push("india");
  if (ww) regs.push("worldwide");
  const next = regs.length ? regs : ["india"];
  const changed = next.join(",") !== getEnabledRegions().join(",");
  enabledRegions = next;
  return changed;
}

// Lane filter for the active list: "" (All) | "worldwide" | "india".
let regionFilter = "";

/** True when a stamped/tag value is marked approximate with a leading ~. */
function stampedApproxPrefix(v) {
  return typeof v === "string" && v.trim().startsWith("~");
}

/** Criterion filters keep missing and ~approx metrics; explicit mismatches fail.
 *  Exception: work-mode is categorical (see jobMatchesWorkModeFilter). */
function listFilterUnsurePasses(unknown, approx) {
  return !!(unknown || approx);
}

function jobMatchesRegion(j) {
  if (!regionFilter) return true;
  const filter = regionFilter === "us" ? "worldwide" : regionFilter;
  const lane = laneForJob(j);
  if (lane === "india" || lane === "worldwide") return lane === filter;
  const loc = j && j.location != null ? String(j.location).trim() : "";
  if (!loc || stampedApproxPrefix(loc)) return true;
  return locationMatchesRegions(loc, [filter], j && j.work_mode);
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
  const cleaned = blob
    .replace(CLEARANCE_EXPLICITLY_NOT_REQUIRED_RE, " ")
    .replace(CLEARANCE_PREFERRED_ONLY_RE, " ");
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

// Shared body for the strict + fallback YOE extractors: identical logic that
// differs only by the range regex and the single-value regex set. `rangeRe`
// seeds min-years-from-ranges (and their spans); each regex in `singleRes` is
// then scanned for a single min value outside those spans. Tenure phrases are
// always skipped. Kept in lock-step with the Python policy module.
function extractMinRequiredYoeWith(text, title, description, rangeRe, singleRes) {
  let blob = [text, title, description].filter(Boolean).map(x => String(x || "")).join(" ");
  if (!blob.trim()) return null;
  blob = blob.replace(/\\\+/g, "+").replace(/\\-/g, "-");
  const isSoft = (start, end) => {
    if (YOE_TENURE_BEFORE_RE.test(blob.slice(Math.max(0, start - 64), start))) return true;
    if (YOE_SOFT_BEFORE_RE.test(blob.slice(Math.max(0, start - 100), start))) return true;
    if (end != null && YOE_SOFT_AFTER_RE.test(blob.slice(end, end + 48))) return true;
    if (yoeMatchIsEducationEquivalent(blob, start, end)) return true;
    return false;
  };
  const mins = [];
  const rangeSpans = [];
  let m;
  rangeRe.lastIndex = 0;
  while ((m = rangeRe.exec(blob)) !== null) {
    if (isSoft(m.index, m.index + m[0].length)) continue;
    const lo = parseInt(m[1], 10);
    const hi = parseInt(m[2], 10);
    mins.push(Math.min(lo, hi));
    rangeSpans.push([m.index, m.index + m[0].length]);
  }
  const inRangeSpan = (start) => rangeSpans.some(([rs, re]) => rs <= start && start < re);
  for (const rx of singleRes) {
    rx.lastIndex = 0;
    while ((m = rx.exec(blob)) !== null) {
      if (inRangeSpan(m.index)) continue;
      if (isSoft(m.index, m.index + m[0].length)) continue;
      mins.push(parseInt(m[1], 10));
    }
  }
  if (!mins.length) return null;
  const sane = mins.filter(n => n > 0 && n <= 40);
  return sane.length ? Math.max(...sane) : null;
}

function extractMinRequiredYoe(text, title, description) {
  return extractMinRequiredYoeWith(text, title, description, YOE_RANGE_RE, [
    YOE_MIN_PLUS_RE, YOE_YEARS_PLUS_RE, YOE_YEARS_EXPERIENCE_RE, YOE_LABEL_RE, YOE_PLAIN_YEARS_EXP_RE,
  ]);
}

function extractMinRequiredYoeFallback(text, title, description) {
  if (extractMinRequiredYoe(text, title, description) != null) return null;
  return extractMinRequiredYoeWith(text, title, description, YOE_FALLBACK_RANGE_RE, [
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
  ]);
}

function requiresUsPerson({ title, description, text } = {}) {
  const blob = [text, title, description].filter(Boolean).map(x => String(x || "")).join(" ");
  if (!blob.trim()) return false;
  const cleaned = blob
    .replace(US_PERSON_PREFERRED_ONLY_RE, " ")
    .replace(US_PERSON_IF_EXPORT_BOILERPLATE_RE, " ");
  return US_PERSON_REQUIRED_RE.test(cleaned);
}

function requiresUsCitizenOrGreencard({ title, description, text } = {}) {
  const blob = [text, title, description].filter(Boolean).map(x => String(x || "")).join(" ");
  if (!blob.trim()) return false;
  if (CITIZENSHIP_OR_GC_REQUIREMENT_RE.test(blob)) return true;
  return requiresUsPerson({ title, description, text });
}

// Shared body for the strict + fallback work-mode detectors: only the regex
// set differs between the two. Kept in lock-step with the Python policy module.
function detectWorkModeWith({ title, location, description } = {}, { hybridRe, remoteRe, onsiteRe }) {
  let blob = [title, location, description].map(x => x || "").join(" ");
  if (!blob.trim()) return "unknown";
  // "Non-Remote" must not match \\bremote\\b (hyphen is a word boundary).
  blob = blob.replace(/\bnon[\s\-]?remote\b/gi, " ");
  if (hybridRe.test(blob)) return "hybrid";
  const remote = remoteRe.test(blob);
  const onsite = onsiteRe.test(blob);
  if (remote && onsite) return "unknown";
  if (remote) return "remote";
  if (onsite) return "onsite";
  return "unknown";
}

function detectWorkMode(args = {}) {
  return detectWorkModeWith(args, {
    hybridRe: WORK_MODE_HYBRID_RE,
    remoteRe: WORK_MODE_REMOTE_RE,
    onsiteRe: WORK_MODE_ONSITE_RE,
  });
}

function detectWorkModeFallback(args = {}) {
  if (detectWorkMode(args) !== "unknown") return "unknown";
  return detectWorkModeWith(args, {
    hybridRe: WORK_MODE_FALLBACK_HYBRID_RE,
    remoteRe: WORK_MODE_FALLBACK_REMOTE_RE,
    onsiteRe: WORK_MODE_FALLBACK_ONSITE_RE,
  });
}

/** Prefer expanded full JD from /description when cached; else preview. */
function jobDescriptionText(job) {
  if (!job) return "";
  const cached = typeof jdCache !== "undefined" ? jdCache.get(job.id) : null;
  if (cached && cached.text) return cached.text;
  return job.job_description || "";
}

/** Strict YOE only — prune / hide / excessive checks. */
function jobMinYoe(job) {
  if (job && job.min_yoe != null && job.min_yoe !== "") {
    const n = Number(job.min_yoe);
    if (!Number.isNaN(n)) return n;
  }
  // Stamped null/empty means backfill looked and found nothing — don't
  // re-parse title ("Engineer (10+ years)") for list filters.
  if (job && Object.prototype.hasOwnProperty.call(job, "min_yoe")) {
    return null;
  }
  return extractMinRequiredYoe(null, job && job.title, jobDescriptionText(job));
}

/** Display YOE: strict first, else fallback. approx=true → prefix ~. */
function jobMinYoeDisplay(job) {
  if (job && Object.prototype.hasOwnProperty.call(job, "min_yoe")) {
    if (job.min_yoe != null && job.min_yoe !== "") {
      const n = Number(job.min_yoe);
      if (!Number.isNaN(n)) return { n, approx: false };
    }
    if (job.min_yoe_fallback != null && job.min_yoe_fallback !== "") {
      const n = Number(job.min_yoe_fallback);
      if (!Number.isNaN(n)) return { n, approx: true };
    }
    return { n: null, approx: false };
  }
  const live = extractMinRequiredYoe(null, job && job.title, jobDescriptionText(job));
  if (live != null) return { n: live, approx: false };
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
  // Stamped work_mode (including "unknown") must not re-detect from a
  // location like "Remote, US" — that flooded the Remote filter.
  if (job && Object.prototype.hasOwnProperty.call(job, "work_mode")) {
    const stampedFb = job.work_mode_fallback;
    if (stampedFb === "remote" || stampedFb === "hybrid" || stampedFb === "onsite") {
      return { mode: stampedFb, approx: true };
    }
    return { mode: "unknown", approx: false };
  }
  const args = {
    title: job && job.title,
    location: job && job.location,
    description: jobDescriptionText(job),
  };
  const strict = resolveListWorkMode(args, { fallback: false });
  if (strict !== "unknown") return { mode: strict, approx: false };
  const stampedFb = job && job.work_mode_fallback;
  if (stampedFb === "remote" || stampedFb === "hybrid" || stampedFb === "onsite") {
    return { mode: stampedFb, approx: true };
  }
  const fb = resolveListWorkMode(args, { fallback: true });
  if (fb !== "unknown") return { mode: fb, approx: true };
  return { mode: "unknown", approx: false };
}

/** Stamp list chips from cached full JD onto the matching jobs[] row (by id). */
function stampListTagsFromCachedJd(jobId) {
  if (!jobId) return false;
  const job = jobs.find(j => j.id === jobId);
  if (!job) return false;
  const cached = jdCache.get(jobId);
  const text = cached && cached.text;
  if (!text) return false;
  let changed = false;
  const title = job.title;
  if (job.salary_min == null || job.salary_min === "") {
    const live = extractSalary(null, title, text);
    if (live && live.min != null) {
      job.salary_min = live.min;
      if (live.max != null) job.salary_max = live.max;
      changed = true;
    } else if (job.salary_min_fallback == null || job.salary_min_fallback === "") {
      const fb = extractSalaryFallback(null, title, text);
      if (fb && fb.min != null) {
        job.salary_min_fallback = fb.min;
        if (fb.max != null) job.salary_max_fallback = fb.max;
        changed = true;
      }
    }
  }
  if (job.min_yoe == null || job.min_yoe === "") {
    const yoe = extractMinRequiredYoe(null, title, text);
    if (yoe != null) {
      job.min_yoe = yoe;
      changed = true;
    } else if (job.min_yoe_fallback == null || job.min_yoe_fallback === "") {
      const yoeFb = extractMinRequiredYoeFallback(null, title, text);
      if (yoeFb != null) {
        job.min_yoe_fallback = yoeFb;
        changed = true;
      }
    }
  }
  if (job.work_mode !== "remote" && job.work_mode !== "hybrid" && job.work_mode !== "onsite") {
    const wm = resolveListWorkMode({ title, location: job.location, description: text });
    if (wm === "remote" || wm === "hybrid" || wm === "onsite") {
      job.work_mode = wm;
      changed = true;
    } else if (
      job.work_mode_fallback !== "remote"
      && job.work_mode_fallback !== "hybrid"
      && job.work_mode_fallback !== "onsite"
    ) {
      const wmFb = resolveListWorkMode(
        { title, location: job.location, description: text },
        { fallback: true },
      );
      if (wmFb === "remote" || wmFb === "hybrid" || wmFb === "onsite") {
        job.work_mode_fallback = wmFb;
        changed = true;
      }
    }
  }
  if (!job.clearance) {
    if (requiresSecurityClearance({
      title,
      company: job.company,
      location: job.location,
      description: text,
      url: job.apply_url || job.job_url || "",
    })) {
      job.clearance = true;
      changed = true;
    }
  }
  if (!job.us_person) {
    if (requiresUsPerson({ title, description: text })) {
      job.us_person = true;
      changed = true;
    }
  }
  return changed;
}

function bindJobListRow(row) {
  if (!row) return;
  const id = row.getAttribute("data-id");
  if (!id) return;
  row.addEventListener("click", e => {
    e.stopPropagation();
    selectJob(id, { appliedFocus: true });
  });
  row.addEventListener("keydown", e => {
    if (e.key === "Enter") selectJob(id, { appliedFocus: true });
  });
  // Hover/focus warm — never blocks list paint; cache hit makes click instant.
  row.addEventListener("pointerenter", () => {
    loadJobDescription(id, { background: true });
  });
  row.addEventListener("focus", () => {
    loadJobDescription(id, { background: true });
  });
}

/** Re-apply list-chip stamps for every id already in jdCache (after poll merge). */
function restampAllFromJdCache() {
  if (typeof jdCache === "undefined" || !jdCache || typeof jdCache.keys !== "function") {
    return;
  }
  for (const id of jdCache.keys()) {
    stampListTagsFromCachedJd(id);
  }
}

/** True when work_mode is a displayable enum. */
function isDeterminedWorkMode(mode) {
  return mode === "remote" || mode === "hybrid" || mode === "onsite";
}

/**
 * Merge /api/jobs payload with local chip stamps so a poll cannot wipe
 * tags that were stamped from jd_full (or a prior slim response) when the
 * incoming row still has unknown/null for that field.
 */
function mergeJobsPreservingListTags(incoming) {
  const prevById = new Map((jobs || []).map(j => [j && j.id, j]));
  return (incoming || []).map(j => {
    if (!j || !j.id) return j;
    const prev = prevById.get(j.id);
    if (!prev) return j;
    const out = j;
    if (!isDeterminedWorkMode(out.work_mode) && isDeterminedWorkMode(prev.work_mode)) {
      out.work_mode = prev.work_mode;
    }
    if (!isDeterminedWorkMode(out.work_mode_fallback)
      && isDeterminedWorkMode(prev.work_mode_fallback)) {
      out.work_mode_fallback = prev.work_mode_fallback;
    }
    if ((out.salary_min == null || out.salary_min === "")
      && prev.salary_min != null && prev.salary_min !== "") {
      out.salary_min = prev.salary_min;
      if (out.salary_max == null || out.salary_max === "") {
        out.salary_max = prev.salary_max;
      }
    }
    if ((out.salary_min_fallback == null || out.salary_min_fallback === "")
      && prev.salary_min_fallback != null && prev.salary_min_fallback !== "") {
      out.salary_min_fallback = prev.salary_min_fallback;
      if (out.salary_max_fallback == null || out.salary_max_fallback === "") {
        out.salary_max_fallback = prev.salary_max_fallback;
      }
    }
    if ((out.min_yoe == null || out.min_yoe === "")
      && prev.min_yoe != null && prev.min_yoe !== "") {
      out.min_yoe = prev.min_yoe;
    }
    if ((out.min_yoe_fallback == null || out.min_yoe_fallback === "")
      && prev.min_yoe_fallback != null && prev.min_yoe_fallback !== "") {
      out.min_yoe_fallback = prev.min_yoe_fallback;
    }
    if (!out.clearance && prev.clearance) out.clearance = true;
    if (!out.us_person && prev.us_person) out.us_person = true;
    return out;
  });
}

/** Re-render one list row after tags stamp — do not wait for a later select. */
function refreshJobListRow(jobId) {
  if (!jobId) return;
  const job = jobs.find(j => j.id === jobId);
  if (!job) return;
  const list = document.getElementById("job-list");
  if (!list) return;
  let row = null;
  list.querySelectorAll(".job-row[data-id]").forEach(el => {
    if (el.getAttribute("data-id") === jobId) row = el;
  });
  if (!row) return;
  const nested = row.classList.contains("nested");
  const showCompany = !!row.querySelector(".co");
  const wrap = document.createElement("div");
  wrap.innerHTML = renderJobRow(job, { nested, showCompany });
  const fresh = wrap.firstElementChild;
  if (!fresh) return;
  row.replaceWith(fresh);
  bindJobListRow(fresh);
  if (typeof syncListSelection === "function") syncListSelection();
}

/** Display salary: INR for India lane; native currency for worldwide. */
function jobSalaryDisplay(job) {
  const lane = laneForJob(job);
  if (lane === "india") {
    const lpaMin = job && job.salary_inr_min_lpa;
    if (lpaMin != null && lpaMin !== "") {
      const n = Number(lpaMin);
      if (!Number.isNaN(n)) {
        const hi = job.salary_inr_max_lpa != null && job.salary_inr_max_lpa !== ""
          ? Number(job.salary_inr_max_lpa) : null;
        return {
          min: n,
          max: hi != null && !Number.isNaN(hi) ? hi : null,
          approx: false,
          currency: "INR",
          display: job.salary_inr_display || job.salary_display || null,
          unit: "lpa",
        };
      }
    }
    if (job && job.salary_inr_display) {
      return { min: null, max: null, approx: false, currency: "INR", display: job.salary_inr_display, unit: "lpa" };
    }
  }
  if (job && job.salary_display && job.salary_currency && job.salary_currency !== "INR") {
    const stampedMin = job.salary_min;
    if (stampedMin != null && stampedMin !== "") {
      const n = Number(stampedMin);
      if (!Number.isNaN(n)) {
        const hi = job.salary_max != null && job.salary_max !== "" ? Number(job.salary_max) : null;
        return {
          min: n,
          max: hi != null && !Number.isNaN(hi) ? hi : null,
          approx: false,
          currency: job.salary_currency || "USD",
          display: job.salary_display,
        };
      }
    }
  }
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
        currency: (job && job.salary_currency) || (lane === "india" ? "INR" : "USD"),
      };
    }
  }
  const live = extractSalary(null, job && job.title, jobDescriptionText(job));
  if (live) return { min: live.min, max: live.max ?? null, approx: false, currency: "USD" };
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
        currency: "USD",
      };
    }
  }
  const fb = extractSalaryFallback(null, job && job.title, jobDescriptionText(job));
  if (fb) return { min: fb.min, max: fb.max ?? null, approx: true, currency: "USD" };
  return { min: null, max: null, approx: false, currency: "USD" };
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

const SALARY_CURRENCY_SYMBOL = {
  USD: "$", EUR: "€", GBP: "£", CAD: "C$", AUD: "A$", CHF: "CHF ",
  SGD: "S$", JPY: "¥", NZD: "NZ$", INR: "₹",
};

/** Format salary for tags / dossier. Currency-aware; INR uses LPA. */
function formatSalaryLabel(min, max, { approx = false, compact = true, currency = "USD", display = null, unit = null } = {}) {
  if (display) return `${approx ? "~" : ""}${display.replace(/^~/, "")}`;
  if (currency === "INR" || unit === "lpa") {
    const lo = min != null && min !== "" ? Number(min) : null;
    const hi = max != null && max !== "" ? Number(max) : null;
    const loOk = lo != null && !Number.isNaN(lo);
    const hiOk = hi != null && !Number.isNaN(hi);
    if (!loOk && !hiOk) return "";
    const fmt = (n) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10));
    let body;
    if (loOk && hiOk && lo !== hi) body = `₹${fmt(lo)}–${fmt(hi)} LPA`;
    else body = `₹${fmt(loOk ? lo : hi)} LPA`;
    return `${approx ? "~" : ""}${body}`;
  }
  const lo = min != null && min !== "" ? Number(min) : null;
  const hi = max != null && max !== "" ? Number(max) : null;
  const loOk = lo != null && !Number.isNaN(lo);
  const hiOk = hi != null && !Number.isNaN(hi);
  if (!loOk && !hiOk) return "";
  const sym = SALARY_CURRENCY_SYMBOL[currency] || (currency ? `${currency} ` : "$");
  const fmt = (n) => {
    if (currency === "JPY") return `${sym}${Math.round(n).toLocaleString("en-US")}`;
    if (compact) return `${sym}${formatCompactSalaryK(n)}`;
    return `${sym}${Math.round(n).toLocaleString("en-US")}`;
  };
  let body;
  if (loOk && hiOk && lo !== hi) body = `${fmt(lo)}–${fmt(hi)}`;
  else body = fmt(loOk ? lo : hi);
  return `${approx ? "~" : ""}${body}`;
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

/** Display labels: sentence case only (Remote / Hybrid / In-person). Enums stay lowercase. */
function formatWorkMode(mode, approx = false) {
  const m = String(mode || "").toLowerCase();
  let label = "";
  if (m === "remote") label = "Remote";
  else if (m === "hybrid") label = "Hybrid";
  else if (m === "onsite" || m === "on-site" || m === "in-person" || m === "in person") {
    label = "In-person";
  } else return "—";
  return `${approx ? "~" : ""}${label}`;
}

/**
 * Resolve work mode for list/dossier chips.
 * Prefer combined title+location+JD; if unknown, prefer JD body over a
 * conflicting aggregator location (e.g. location "Remote" vs onsite JD).
 */
function resolveListWorkMode({ title, location, description } = {}, { fallback = false } = {}) {
  const detect = fallback ? detectWorkModeFallback : detectWorkMode;
  const modes = new Set(["remote", "hybrid", "onsite"]);
  const combined = detect({ title, location, description });
  if (modes.has(combined)) return combined;
  if (description && String(description).trim()) {
    const body = detect({ title, location: "", description });
    if (modes.has(body)) return body;
  }
  if (location && String(location).trim()) {
    const locOnly = detect({ title, location, description: "" });
    if (modes.has(locOnly)) return locOnly;
  }
  return "unknown";
}

function isStaleListing(job) {
  // Exact date_posted only (via jobPostedDisplay). Never ~ fallback, never created_at.
  const { time, approx } = jobPostedDisplay(job);
  if (time == null || approx) return false;
  const ageDays = (Date.now() - time) / 86400000;
  return ageDays > STALE_LISTING_MAX_AGE_DAYS;
}

function isNeedsUrlListing(job) {
  // Recovered resume-dir stubs (or explicit needs_url) have no apply URL yet.
  // Keep them in All / Deleted / Applied; only keep them out of Open counts.
  if (!job) return false;
  if (job.needs_url === true) return true;
  const src = String(job.source || "").toLowerCase();
  return src === "recovered" && !applicationHref(job);
}

function isHiddenUntouchedListing(job) {
  // Only ever applies to a job that's still exactly where discovery left
  // it - never to anything already started, stuck, reviewed, or applied.
  // Excessive YOE / citizen-GC are a client safety net; server backfill
  // also moves them to deleted.
  // Note: URL-less recovered stubs are NOT hidden here — see isNeedsUrlListing
  // (Open queue / Open counts only) so All can still surface them.
  return job.status === "discovered" && (
    isExcludedTitle(job.title)
    || isStaleListing(job)
    || !locationMatchesRegions(job.location, getEnabledRegions(), job.work_mode)
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
/** Lazy-loaded cleaned JDs: jobId -> { loading, text, error, source } */
const jdCache = new Map();
/** In-flight GET /description promises — dedupe click + prefetch. */
const jdInflight = new Map();
/** Cap idle prefetch so we never pull ~6k full JDs. */
const JD_PREFETCH_MAX = 48;
const JD_PREFETCH_CHUNK = 6;
const JD_RECENT_MAX = 12;
let _jdPrefetchGen = 0;
let _jdPrefetchIdleHandle = null;
/** Recently opened dossier ids (ring) — warm these first. */
const _jdRecentlyViewed = [];
/** Job id whose JD copy button is showing the transient "copied" checkmark. */
let jdCopyFlashJobId = null;
let jdCopyFlashTimer = null;
let jdEditJobId = null;
let jdEditDraft = "";
let jdEditSaving = false;
let queue = "open"; // stuck | ready | progress | open | applied | deleted | all
/** Company key when "Same company" siblings panel is open in the dossier. */
let siblingsPanelCompany = null;
/** Job id when the Resume expand-below preview panel is open. */
let resumePanelJobId = null;
/** Job id when the inline LaTeX editor below the action row is open. */
let resumeLatexPanelJobId = null;
/** Job id when the Fast copy panel below the action row is open. */
let copyKitPanelJobId = null;
/** jobId -> { loading, error, kit, testMode } */
const copyKitCache = new Map();
let copyKitCopiedKey = null;
let copyKitCopiedTimer = null;
/** Unsaved editor state survives normal dashboard poll re-renders. */
const resumeLatexDrafts = new Map();
/** Session + localStorage: treat job as having a resume (skip tailor UX only). */
const TREAT_RESUME_STORAGE_KEY = "jobHunterTreatResumeOnFile";
function loadTreatResumeOnFile() {
  try {
    const raw = localStorage.getItem(TREAT_RESUME_STORAGE_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr.filter((x) => typeof x === "string") : []);
  } catch (_) {
    return new Set();
  }
}
function saveTreatResumeOnFile() {
  try {
    localStorage.setItem(TREAT_RESUME_STORAGE_KEY, JSON.stringify([...treatResumeOnFile]));
  } catch (_) { /* ignore */ }
}
const treatResumeOnFile = loadTreatResumeOnFile();
/** Per-job Fill popover selection: "with-resume" | "tailor". */
const selectedFillModeByJob = new Map();
/** Server: headed fill/CAPTCHA/Ready hold still live (UI-008). */
let fillHoldActive = false;
let searchText = "";
/** Map token → Set(jobId) from GET /api/jobs/search (JD body hits). */
let jdSearchTokenHits = null;
let jdSearchGen = 0;
let sourceFilter = "";
let groupBy = "none"; // none | company | source
let sortBy = "date"; // date | company | status | yoe | salary | salary_asc | multi_opening
let workModeFilter = ""; // "" | remote | hybrid | onsite | unknown
let yoeFilter = ""; // "" | le3 | le5 | le6 | has | unknown
let dateFilter = ""; // "" | 1d | 2d | 3d | 7d | 14d | 30d | older
let salaryFilter = ""; // "" | has | unknown | ge100 | ge150 | ge200 | le120
/** Merged apply-URL + multi flags + missing JD. */
let extrasFilter = ""; // "" | has_url | missing_url | multi_opening | multi_source | no_jd
let expandedGroups = new Set();
let appliedSortKey = "date";
let appliedSortDir = "desc";
let editingAppliedId = null;
let editingApplyUrlId = null;
let scrollToAppliedDetail = false;
/** Applied queue: hide center tracking table when a sidebar job is selected (session-only). */
let appliedTableHidden = false;
let lastPollAt = null;

/** Per-family list-filter map in localStorage. Survives tabs, refresh, sessions. */
const FILTER_STATE_KEY = "opsFilterState";
/** Legacy per-queue map (sessionStorage); migrated into FILTER_STATE_KEY. */
const FILTER_STATE_BY_QUEUE_KEY = "opsFilterStateByQueue";
/** Storage families: Open, Applied, pipeline (Stuck/Ready/In progress), Deleted. */
const FILTER_FAMILY_KEYS = ["open", "applied", "pipeline", "deleted"];
/** Old sidebar queues that shared one pipeline family before unification. */
const LEGACY_PIPELINE_QUEUE_KEYS = ["stuck", "ready", "progress"];
const TL_KEY = "ops-timeline-collapsed";
/** Auto-collapse after the user expands the timeline (ms). */
const TL_AUTO_COLLAPSE_MS = 10000;
/** Always start closed; expanded state is temporary (timer / focus-loss). */
let timelineCollapsed = true;
let _timelineAutoCollapseTimer = null;
try {
  // Prefer stored preference only when it says collapsed; never boot expanded.
  if (localStorage.getItem(TL_KEY) === "0") {
    /* ignore — start closed every session */
  }
  localStorage.setItem(TL_KEY, "1");
} catch (_) { /* private mode / storage blocked */ }

function migrateExtrasFromLegacy(s) {
  if (typeof s.extras === "string" && s.extras) return s.extras;
  if (s.applyUrl === "has") return "has_url";
  if (s.applyUrl === "missing") return "missing_url";
  if (s.multi === "multi_opening" || s.multi === "multi_source") return s.multi;
  return "";
}

/** Every filter global as one plain object (the stored / snapshot shape). */
function captureFilterState() {
  return {
    search: searchText,
    source: sourceFilter,
    group: groupBy,
    sort: sortBy,
    mode: workModeFilter,
    yoe: yoeFilter,
    date: dateFilter,
    salary: salaryFilter,
    extras: extrasFilter,
    region: regionFilter,
  };
}

/** Inverse of captureFilterState(); a missing/partial object means defaults. */
function applyFilterState(state) {
  const s = state && typeof state === "object" ? state : {};
  const str = (v) => (typeof v === "string" ? v : "");
  searchText = str(s.search);
  sourceFilter = str(s.source);
  groupBy = str(s.group) || "none";
  sortBy = str(s.sort) || "date";
  workModeFilter = str(s.mode);
  yoeFilter = str(s.yoe);
  dateFilter = str(s.date);
  salaryFilter = str(s.salary);
  extrasFilter = migrateExtrasFromLegacy(s);
  regionFilter = str(s.region);
}

/** Today preset: India + ≤5 YOE + posted last 2 days. */
const TODAY_FILTER_PRESET = {
  source: "",
  group: "none",
  sort: "date",
  mode: "",
  yoe: "le5",
  date: "2d",
  salary: "",
  extras: "",
  region: "india",
};

/** In-memory map: family key -> captureFilterState() blob. */
let filterStateByFamily = {};

function filterFamilyForQueue(q) {
  if (q === "applied") return "applied";
  if (q === "deleted") return "deleted";
  if (LEGACY_PIPELINE_QUEUE_KEYS.includes(q)) return "pipeline";
  return "open";
}

function parseStoredObject(raw) {
  if (!raw) return null;
  try {
    const s = JSON.parse(raw);
    return s && typeof s === "object" && !Array.isArray(s) ? s : null;
  } catch (_) {
    return null;
  }
}

/** True when `s` is a { open: {...}, pipeline: {...} } per-family map. */
function isPerFamilyFilterMap(s) {
  if (!s || typeof s !== "object" || Array.isArray(s)) return false;
  const keys = [...FILTER_FAMILY_KEYS, ...LEGACY_PIPELINE_QUEUE_KEYS, "deleted"];
  return keys.some(
    q => s[q] && typeof s[q] === "object" && !Array.isArray(s[q]),
  );
}

/** Flat blob from the old one-global-filter era (search/source/sort at top level). */
function isFlatFilterBlob(s) {
  if (!s || typeof s !== "object" || Array.isArray(s)) return false;
  if (isPerFamilyFilterMap(s)) return false;
  return (
    "search" in s || "source" in s || "sort" in s || "mode" in s
    || "yoe" in s || "date" in s || "salary" in s || "region" in s
  );
}

/** Merge legacy stuck/ready/progress keys into one pipeline family. */
function normalizeStoredFilterMap(raw) {
  const out = {};
  if (!raw || typeof raw !== "object") return out;
  for (const k of FILTER_FAMILY_KEYS) {
    if (raw[k] && typeof raw[k] === "object" && !Array.isArray(raw[k])) {
      out[k] = raw[k];
    }
  }
  let bestPipeline = out.pipeline || null;
  let bestN = bestPipeline ? filterStateActivity(bestPipeline) : -1;
  for (const k of LEGACY_PIPELINE_QUEUE_KEYS) {
    const s = raw[k];
    if (!s || typeof s !== "object" || Array.isArray(s)) continue;
    const n = filterStateActivity(s);
    if (n > bestN) {
      bestN = n;
      bestPipeline = s;
    }
  }
  if (bestPipeline) out.pipeline = bestPipeline;
  return out;
}

function filterStateActivity(s) {
  if (!s || typeof s !== "object") return 0;
  let n = 0;
  if (s.search) n++;
  if (s.source) n++;
  if (s.group && s.group !== "none") n++;
  if (s.sort && s.sort !== "date") n++;
  if (s.mode) n++;
  if (s.yoe) n++;
  if (s.date) n++;
  if (s.salary) n++;
  if (s.extras) n++;
  if (s.region) n++;
  return n;
}

function readStoredFilterMap() {
  const local = parseStoredObject(storageGet(FILTER_STATE_KEY));
  if (isPerFamilyFilterMap(local)) {
    return { map: normalizeStoredFilterMap(local), persist: false };
  }
  if (isFlatFilterBlob(local)) {
    return { map: { open: { ...local } }, persist: true };
  }
  const localByQueue = parseStoredObject(storageGet(FILTER_STATE_BY_QUEUE_KEY));
  if (isPerFamilyFilterMap(localByQueue)) {
    return { map: normalizeStoredFilterMap(localByQueue), persist: true };
  }
  const sessMap = parseStoredObject(
    storageGet(FILTER_STATE_BY_QUEUE_KEY, sessionStorage),
  );
  if (isPerFamilyFilterMap(sessMap)) {
    return { map: normalizeStoredFilterMap(sessMap), persist: true };
  }
  const sessFlat = parseStoredObject(storageGet(FILTER_STATE_KEY, sessionStorage));
  if (isFlatFilterBlob(sessFlat)) {
    return { map: { open: { ...sessFlat } }, persist: true };
  }
  if (isPerFamilyFilterMap(sessFlat)) {
    return { map: normalizeStoredFilterMap(sessFlat), persist: true };
  }
  return { map: {}, persist: false };
}

function persistFilterMap() {
  storageSet(FILTER_STATE_KEY, JSON.stringify(filterStateByFamily));
}

function loadFilterState() {
  const { map, persist } = readStoredFilterMap();
  filterStateByFamily = map;
  applyFilterState(filterStateByFamily[filterFamilyForQueue(queue)] || {});
  if (persist) persistFilterMap();
}

function saveFilterState() {
  filterStateByFamily[filterFamilyForQueue(queue)] = captureFilterState();
  persistFilterMap();
}

/** Save current family's filters and restore the target family's on queue switch. */
function swapQueueFilterState(nextQueue) {
  const curFamily = filterFamilyForQueue(queue);
  const nextFamily = filterFamilyForQueue(nextQueue);
  filterStateByFamily[curFamily] = captureFilterState();
  if (curFamily !== nextFamily) {
    applyFilterState(filterStateByFamily[nextFamily] || {});
    syncFilterControlsFromState();
    scheduleJdSearch();
  }
}

/** Clears every list filter; the empty set is what persists. */
function clearListFilters() {
  applyFilterState(null);
  const searchEl = document.getElementById("search");
  if (searchEl) searchEl.value = "";
  jdSearchTokenHits = null;
  jdSearchGen++;
  syncFilterControlsFromState();
  saveFilterState();
  updateFiltersChrome();
  render();
}

function applyTodayFilterPreset() {
  applyFilterState({ ...TODAY_FILTER_PRESET, search: searchText });
  syncFilterControlsFromState();
  saveFilterState();
  updateFiltersChrome();
  render();
  scheduleJdSearch();
}

function syncFilterControlsFromState() {
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val;
  };
  set("search", searchText);
  set("source-filter", sourceFilter);
  set("group-by", groupBy);
  set("sort-by", sortBy);
  set("work-mode-filter", workModeFilter);
  set("yoe-filter", yoeFilter);
  set("date-filter", dateFilter);
  set("salary-filter", salaryFilter);
  set("extras-filter", extrasFilter);
  set("region-filter", regionFilter);
}

function filtersAreActive() {
  return activeFilterCount() > 0;
}

function activeFilterCount() {
  let n = 0;
  if (searchText) n++;
  if (sourceFilter) n++;
  if (groupBy !== "none") n++;
  if (sortBy !== "date") n++;
  if (workModeFilter) n++;
  if (yoeFilter) n++;
  if (dateFilter) n++;
  if (salaryFilter) n++;
  if (extrasFilter) n++;
  if (regionFilter) n++;
  return n;
}

function filtersToggleLabel(active, visible) {
  return active > 0 ? `Filters · ${active} · ${visible}` : `Filters · ${visible}`;
}

function filtersToggleTitle(active, visible) {
  const jobPart = `${visible} ${visible === 1 ? "job" : "jobs"} in this list`;
  if (active <= 0) return jobPart;
  const filterPart = `${active} ${active === 1 ? "filter" : "filters"}`;
  return `${filterPart} · ${jobPart}`;
}

function updateFiltersChrome(visibleCount) {
  const n = activeFilterCount();
  const vis = visibleCount == null ? visibleJobs().length : visibleCount;
  const label = document.getElementById("filters-toggle-label");
  const toggle = document.getElementById("filters-toggle");
  const popClear = document.getElementById("filters-popover-clear");
  const title = filtersToggleTitle(n, vis);
  if (label) label.textContent = filtersToggleLabel(n, vis);
  if (toggle) {
    toggle.classList.toggle("has-active", n > 0);
    toggle.title = title;
    toggle.setAttribute("aria-label", title);
  }
  if (popClear) popClear.disabled = n === 0;
}

const FILTERS_HOVER_OPEN_MS = 667;
let filtersHoverOpenTimer = null;

function clearFiltersHoverOpenTimer() {
  if (filtersHoverOpenTimer == null) return;
  clearTimeout(filtersHoverOpenTimer);
  filtersHoverOpenTimer = null;
}

function setFiltersPopoverOpen(open) {
  const wrap = document.getElementById("list-filters");
  const toggle = document.getElementById("filters-toggle");
  if (!wrap || !toggle) return;
  clearFiltersHoverOpenTimer();
  wrap.classList.toggle("open", !!open);
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
  if (!open) {
    const ae = document.activeElement;
    if (ae && wrap.contains(ae) && typeof ae.blur === "function") ae.blur();
  }
}

function filtersPopoverIsVisible() {
  const wrap = document.getElementById("list-filters");
  return !!(wrap && wrap.classList.contains("open"));
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

loadFilterState();

const TEST_MODE_STORAGE_KEY = "jobHunterTestMode";
const PARTYROCK_STORAGE_KEY = "jobHunterPartyRock";
// India-only discovery sources (mirror server INDIA_ONLY_SOURCE_IDS): only run
// when the India region is on; greyed/forced-off in the popover otherwise.
const INDIA_ONLY_SOURCE_IDS = [
  "internshala", "hirist", "cutshort", "shine", "freshersworld", "naukri", "adzuna",
];
// Keep in sync with dashboard/discovery_sources.py DISCOVERY_SOURCE_DEFS.
const DISCOVERY_SOURCE_CATALOG = [
  { id: "indeed", label: "Indeed", recency: true, lane: "shared", scrape_status: "active" },
  { id: "linkedin", label: "LinkedIn", recency: true, lane: "shared", scrape_status: "active" },
  { id: "internshala", label: "Internshala", india_only: true, lane: "india", scrape_status: "active" },
  { id: "hirist", label: "Hirist", india_only: true, lane: "india", scrape_status: "active" },
  { id: "cutshort", label: "Cutshort", india_only: true, lane: "india", scrape_status: "active" },
  { id: "shine", label: "Shine", india_only: true, lane: "india", scrape_status: "active" },
  { id: "freshersworld", label: "Freshersworld", india_only: true, lane: "india", scrape_status: "active" },
  { id: "naukri", label: "Naukri", india_only: true, lane: "india", scrape_status: "active" },
  { id: "adzuna", label: "Adzuna (IN)", india_only: true, recency: true, lane: "india", scrape_status: "api" },
  { id: "angellist_india", label: "AngelList India", india_only: true, lane: "india", scrape_status: "catalog" },
  { id: "remoteok", label: "RemoteOK", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "remotive", label: "Remotive", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "jobicy", label: "Jobicy", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "rss_feeds", label: "RSS feeds (bundle)", worldwide_only: true, lane: "worldwide", scrape_status: "rss" },
  { id: "himalayas", label: "Himalayas", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "weworkremotely", label: "We Work Remotely", worldwide_only: true, lane: "worldwide", scrape_status: "rss" },
  { id: "jobspresso", label: "Jobspresso", worldwide_only: true, lane: "worldwide", scrape_status: "rss" },
  { id: "authentic_jobs", label: "Authentic Jobs", worldwide_only: true, lane: "worldwide", scrape_status: "rss" },
  { id: "nodesk", label: "NoDesk", worldwide_only: true, lane: "worldwide", scrape_status: "rss" },
  { id: "landing_jobs", label: "Landing.jobs", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "jsremotely", label: "JS Remotely", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "working_nomads", label: "Working Nomads", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "europeremotely", label: "EuropeRemotely", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "arbeitnow", label: "Arbeitnow", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "relocate_me", label: "relocate.me", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "germanstartups", label: "German Startups Jobs", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "justremote", label: "JustRemote", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "dynamitejobs", label: "Dynamite Jobs", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "wellfound", label: "Wellfound", worldwide_only: true, lane: "worldwide", scrape_status: "blocked_captcha" },
  { id: "otta", label: "Otta", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
  { id: "yc_jobs", label: "Y Combinator Jobs", worldwide_only: true, lane: "worldwide", scrape_status: "active" },
  { id: "turing", label: "Turing", worldwide_only: true, lane: "worldwide", scrape_status: "needs_account" },
  { id: "angelhub", label: "AngelHub", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
  { id: "producthunt_jobs", label: "Product Hunt Jobs", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
  { id: "remotetechjobs", label: "RemoteTechJobs", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
  { id: "outsourcely", label: "Outsourcely", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
  { id: "hubstaff_talent", label: "Hubstaff Talent", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
  { id: "workew", label: "Workew", worldwide_only: true, lane: "worldwide", scrape_status: "rss" },
  { id: "pangian", label: "Pangian", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
  { id: "hired", label: "Hired", worldwide_only: true, lane: "worldwide", scrape_status: "needs_account" },
  { id: "themuse", label: "The Muse", worldwide_only: true, lane: "worldwide", scrape_status: "api" },
  { id: "jooble", label: "Jooble", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
  { id: "topaijobs", label: "TopAIJobs", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
  { id: "crossover", label: "Crossover", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
  { id: "jobbatical", label: "Jobbatical", worldwide_only: true, lane: "worldwide", scrape_status: "catalog" },
];
function isIndiaOnlySource(id) {
  return INDIA_ONLY_SOURCE_IDS.includes(id)
    || DISCOVERY_SOURCE_CATALOG.some(c => c.id === id && c.india_only);
}
const DISCOVERY_SOURCES_STORAGE_KEY = "jobHunterDiscoverySources";
const SOURCE_DAYS_MIN = 1;
const SOURCE_DAYS_MAX = 10;

const STUCK_STATUSES = new Set(["stuck", "blocked_captcha"]);
const READY_STATUSES = new Set(["ready_for_review"]);
const PROGRESS_STATUSES = new Set([...ACTIVE_PROGRESS_STATUSES, "resume_ready"]);
/** Ready / CAPTCHA hold blocks Fill while browser still held (UI-002/008). */
const HOLD_BUSY_STATUSES = new Set(["ready_for_review", "blocked_captcha"]);
const OPEN_STATUSES = new Set(["discovered"]);
/** Legacy holding-pen statuses — treated as Deleted until/after migration. */
const LEGACY_SKIPPED_STATUSES = new Set([
  "skipped_manual", "skipped_duplicate", "skipped_contract", "skipped_easy_apply",
]);

function storageGet(key, storage = localStorage) {
  try { return storage.getItem(key); } catch (_) { return null; }
}

function storageSet(key, value, storage = localStorage) {
  try { storage.setItem(key, value); } catch (_) { /* quota / blocked */ }
}

function loadTestModeSetting() {
  const raw = storageGet(TEST_MODE_STORAGE_KEY);
  if (raw === null) return true;
  return raw !== "0" && raw !== "false";
}

function saveTestModeSetting(on) {
  storageSet(TEST_MODE_STORAGE_KEY, on ? "1" : "0");
}

function loadPartyRockSetting() {
  const raw = storageGet(PARTYROCK_STORAGE_KEY);
  if (raw === null) return true;
  return raw !== "0" && raw !== "false";
}

function savePartyRockSetting(on) {
  storageSet(PARTYROCK_STORAGE_KEY, on ? "1" : "0");
}

function defaultDiscoverySourceMap() {
  const m = {};
  const runnable = new Set(["active", "rss", "api"]);
  for (const s of DISCOVERY_SOURCE_CATALOG) {
    // Runnable boards on by default; catalog/blocked/dead off until enabled.
    m[s.id] = runnable.has(s.scrape_status || "active");
  }
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

function sourceSupportsRecency(source) {
  if (source && typeof source.recency === "boolean") return source.recency;
  const id = typeof source === "string" ? source : source?.id;
  return DISCOVERY_SOURCE_CATALOG.some(s => s.id === id && s.recency);
}

function clampSourceDays(value, fallback = 7) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(SOURCE_DAYS_MIN, Math.min(SOURCE_DAYS_MAX, Math.round(n)));
}

function effectiveSourceDays(sourceId, disc) {
  const pinned = disc?.source_days?.[sourceId];
  if (pinned != null && pinned !== "") return clampSourceDays(pinned);
  const adaptive = Number(disc?.adaptive_recency_days || disc?.builtin_days_since_updated || 7);
  return clampSourceDays(adaptive, 7);
}

async function saveSourceDaysSetting(sourceId, value) {
  const days = Number(value);
  if (days < SOURCE_DAYS_MIN || days > SOURCE_DAYS_MAX) return;
  const { ok, data } = await apiPost("/api/discover/settings", { source_days: { [sourceId]: days } }, {
    onError: (err) => {
      alert(err.error || "Could not save lookback period.");
      renderDiscoverPopover(discoveryState);
    },
  });
  if (!ok) return;
  discoveryState = {
    ...(discoveryState || {}),
    source_days: data.source_days || { ...((discoveryState || {}).source_days || {}), [sourceId]: days },
  };
}

function toggleDiscoverySource(sourceId, checked) {
  const map = loadDiscoverySourceSettings();
  map[sourceId] = !!checked;
  // Keep at least one source on — re-check if user turned the last one off.
  if (!Object.values(map).some(Boolean)) {
    map[sourceId] = true;
    alert("Enable at least one discovery source.");
  }
  saveDiscoverySourceSettings(map);
  renderDiscoverPopover(discoveryState);
}

// Discover popover India / Worldwide lane toggles.
async function toggleDiscoverRegion(region, checked) {
  const regs = getEnabledRegions();
  let worldwide = regs.includes("worldwide") || regs.includes("us");
  let india = regs.includes("india");
  if (region === "worldwide" || region === "us") worldwide = !!checked;
  else if (region === "india") india = !!checked;
  if (!worldwide && !india) {
    india = true;
    alert("Keep at least one lane on (India is the default).");
  }
  if (region === "india" && india) {
    const map = loadDiscoverySourceSettings();
    for (const id of INDIA_ONLY_SOURCE_IDS) map[id] = true;
    saveDiscoverySourceSettings(map);
  }
  const { ok, data } = await apiPost("/api/discover/settings", {
    discover_worldwide: worldwide,
    discover_india: india,
  }, {
    onError: (d) => {
      alert(d.error || "Could not save lane settings.");
      renderDiscoverPopover(discoveryState);
    },
  });
  if (!ok) return;
  discoveryState = {
    ...(discoveryState || {}),
    discover_worldwide: data.discover_worldwide !== undefined ? data.discover_worldwide : worldwide,
    discover_india: data.discover_india !== undefined ? data.discover_india : india,
  };
  updateEnabledRegionsFromDiscovery(discoveryState);
  renderDiscoverPopover(discoveryState);
  render();
}

let testModeEnabled = loadTestModeSetting();
let partyRockEnabled = loadPartyRockSetting();

function statusLabel(s) {
  return (s || "unknown").replaceAll("_", " ");
}

function queueBucket(status) {
  if (status === "deleted" || LEGACY_SKIPPED_STATUSES.has(status)) return "deleted";
  if (STUCK_STATUSES.has(status)) return "stuck";
  if (READY_STATUSES.has(status)) return "ready";
  if (PROGRESS_STATUSES.has(status)) return "progress";
  if (OPEN_STATUSES.has(status)) return "open";
  // Leftover cancelled statuses migrate to Open server-side.
  if (status === "cancelled") return "open";
  if (status === "applied") return "applied";
  return "open";
}

/** Sidebar square + banner pulse: live pipeline vs parked waiting-on-you.
 *  active/orange: tailoring, navigating, filling, resuming
 *  ready/green:   resume_ready, ready_for_review, blocked_captcha
 *  none:          Open/applied/stuck/deleted — keep queueBucket rail color
 */
function jobActivityDot(job) {
  const status = (job && job.status) || "";
  if (ACTIVE_PROGRESS_STATUSES.has(status)) return "active";
  if (status === "resume_ready" || HOLD_BUSY_STATUSES.has(status)) return "ready";
  return "";
}

/** Normalize aliases so clearance / clearance_or_intel share one group, etc. */
function normalizeDeletedReasonCode(code) {
  const key = String(code || "").trim().toLowerCase();
  if (!key) return "";
  if (key === "clearance") return "clearance_or_intel";
  if (key === "seniority") return "management_track";
  if (key === "non_us") return "non_us_location";
  if (key === "manual") return "user";
  if (key === "apply_resolve_failed") return "unresolved_apply_url";
  if (key === "dead_apply_url") return "closed_posting";
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
  // Keep concrete dead/404 and closed/lever codes readable as-is.
  const raw = String(code).trim();
  if (/^(dead|closed)\//i.test(raw)) {
    return short ? raw.toLowerCase() : raw.toLowerCase();
  }
  const human = raw.replaceAll("_", " ");
  if (!human) return short ? "No reason" : "";
  return short ? human.replace(/\b\w/g, c => c.toUpperCase()) : human;
}

/** Human-readable reason(s); null when none (omit in compact UI). */
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

function normalizeCompanyName(name) {
  // Keep in lockstep with scripts/text_normalize.py normalize_company().
  let s = String(name || "").toLowerCase();
  s = s.replace(/\b(inc|llc|corp|corporation|ltd|co|company|group|technologies|technology)\b\.?/g, "");
  s = s.replace(/[^a-z0-9]+/g, "");
  return s;
}

function companyKey(job) {
  const persisted = String((job && job.company_key) || "").trim();
  if (persisted) return persisted;
  return normalizeCompanyName(job && job.company) || "(unknown)";
}

// The age column is a *posted* age: postedAgeDays / postedAgeLabel come from
// job_sort.js, so the label and the Posted sort always read the same resolved
// date. It must never fall back to created_at - an undated row that labels
// itself "0d" while sorting to the bottom is exactly what made Posted look
// unsorted.

function fillOutcome(job, { full = false } = {}) {
  const d = (job.status_detail || "").trim();
  if (!d) return null;
  const bucket = queueBucket(job.status);
  if (bucket === "stuck" || bucket === "ready" || bucket === "progress") {
    const one = d.split(/[\n\r]/)[0].trim();
    // List rows stay compact; dossier identity band shows the full status_detail.
    if (full) return one;
    return one.length > 120 ? one.slice(0, 117) + "…" : one;
  }
  return null;
}

/** Ashby anti-spam flag guidance when fill status_detail mentions it. */
function ashbySpamHintHtml(job) {
  const d = (job.status_detail || "").toLowerCase();
  if (
    !d.includes("ashby spam")
    && !d.includes("possible spam")
    && !d.includes("ashby_spam")
  ) {
    return "";
  }
  return `<div class="fill-outcome" style="border-color:#e8913a;color:#fcd9b6">`
    + `Ashby flagged this browser session as possible spam. `
    + `Close the fill window → Start again (fresh session), or submit from `
    + `Chrome incognito with the apply URL. Reloading the same tab usually `
    + `does not clear the flag.</div>`;
}

/** companyKey -> count of jobs with status applied (rebuilt each render). */
let companyAppliedCounts = new Map();

function rebuildCompanyAppliedCounts() {
  const counts = new Map();
  for (const j of jobs) {
    if (j.status !== "applied") continue;
    const key = companyKey(j);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  companyAppliedCounts = counts;
}

function companyApplyCountLookup(companyOrJob) {
  const key = companyOrJob && typeof companyOrJob === "object"
    ? companyKey(companyOrJob)
    : (normalizeCompanyName(companyOrJob) || "(unknown)");
  return companyAppliedCounts.get(key) || 0;
}

function companyApplyCountBadgeHtml(companyOrJob) {
  const n = companyApplyCountLookup(companyOrJob);
  if (!n) return "";
  const title = n === 1 ? "Applied 1 time to this company" : `Applied ${n} times to this company`;
  return `<span class="tag applied-count" title="${escapeAttr(title)}">${n}x</span>`;
}

function companyAppliedInfo(company) {
  const key = normalizeCompanyName(company);
  if (!key) return null;
  const count = companyApplyCountLookup(key);
  if (!count) return null;
  const applied = jobs.filter(j => j.status === "applied" && companyKey(j) === key);
  let latest = 0;
  for (const j of applied) {
    const t = Date.parse(j.updated_at || j.created_at || "") || 0;
    if (t > latest) latest = t;
  }
  const lastDays = latest ? Math.max(0, Math.floor((Date.now() - latest) / 86400000)) : null;
  return { count, lastDays };
}

/** Other non-skipped listings at the same company (excludes `job`).
 * Applies the same title / non-US / clearance / YOE / citizen-GC filters
 * as discovery — per sibling only (not stale). Senior/junior/mid stay;
 * civilian Security Engineer without clearance language stays.
 */
function companySiblings(job) {
  const key = companyKey(job);
  return jobs.filter(j => {
    if (j.id === job.id) return false;
    if (companyKey(j) !== key) return false;
    if (LEGACY_SKIPPED_STATUSES.has(j.status) || j.status === "deleted") return false;
    if (isExcludedTitle(j.title)) return false;
    if (!locationMatchesRegions(j.location, getEnabledRegions(), j.work_mode)) return false;
    if (jobRequiresClearance(j)) return false;
    if (jobRequiresExcessiveYoe(j)) return false;
    if (jobRequiresCitizenOrGc(j)) return false;
    return true;
  });
}

function jobMatchesQueue(j) {
  const b = queueBucket(j.status);
  switch (queue) {
    case "stuck": return b === "stuck";
    case "ready": return b === "ready";
    case "progress": return b === "progress";
    case "open": return b === "open" && !isNeedsUrlListing(j);
    case "skipped": return false; // Skipped holding pen removed — use Deleted
    case "applied": return b === "applied";
    case "deleted": return b === "deleted";
    case "all": return b !== "applied" && b !== "deleted";
    default: return true;
  }
}

function jobMatchesWorkModeFilter(j) {
  if (!workModeFilter) return true;
  // Categorical mode: require Remote/~Remote (etc.). Unlike YOE/pay/date,
  // unknown and other modes do not pass — Remote must not flood with
  // unstamped or clearly Hybrid/In-person rows.
  if (stampedApproxPrefix(j && j.work_mode)) {
    const raw = String(j.work_mode).trim().replace(/^~+/, "").toLowerCase();
    if (workModeFilter === "unknown") return false;
    return raw === workModeFilter;
  }
  const { mode } = jobWorkModeDisplay(j);
  if (workModeFilter === "unknown") return mode === "unknown";
  return mode === workModeFilter;
}

function jobMatchesYoeFilter(j) {
  if (!yoeFilter) return true;
  const { n: ymin, approx } = jobMinYoeDisplay(j);
  if (yoeFilter === "has") return ymin != null;
  if (yoeFilter === "unknown") return ymin == null;
  if (listFilterUnsurePasses(ymin == null, approx)) return true;
  if (yoeFilter === "le3") return ymin <= 3;
  if (yoeFilter === "le5") return ymin <= 5;
  if (yoeFilter === "le6") return ymin <= 6;
  return true;
}

function jobMatchesDateFilter(j) {
  if (!dateFilter) return true;
  const posted = typeof jobPostedDisplay === "function"
    ? jobPostedDisplay(j)
    : { time: datePostedTime(j), approx: false };
  const t = posted.time;
  const unknown = t == null;
  const approx = !!posted.approx;
  if (listFilterUnsurePasses(unknown, approx)) return true;
  const ageDays = (Date.now() - t) / 86400000;
  if (dateFilter === "older") return ageDays > 30;
  const windowMatch = /^(\d+)d$/.exec(dateFilter);
  if (windowMatch) return ageDays <= Number(windowMatch[1]);
  return true;
}

function jobMatchesSalaryFilter(j) {
  if (!salaryFilter) return true;
  const info = jobSalaryDisplay(j);
  const { min, approx, unit, currency } = info;
  if (salaryFilter === "has") return min != null || !!info.display;
  if (salaryFilter === "unknown") return min == null && !info.display;
  if (listFilterUnsurePasses(min == null, approx)) return true;
  // India LPA thresholds paired with USD bands in the filter labels.
  if (unit === "lpa" || currency === "INR") {
    if (salaryFilter === "ge100") return min >= 15;
    if (salaryFilter === "ge150") return min >= 25;
    if (salaryFilter === "ge200") return min >= 40;
    if (salaryFilter === "le120") return min <= 20;
    return true;
  }
  if (salaryFilter === "ge100") return min >= 100000;
  if (salaryFilter === "ge150") return min >= 150000;
  if (salaryFilter === "ge200") return min >= 200000;
  if (salaryFilter === "le120") return min <= 120000;
  return true;
}

/** List rows omit JD bodies; prefer has_description, then cache, then preview. */
function jobHasDescription(job) {
  if (!job) return false;
  try {
    if (typeof jdCache !== "undefined" && jdCache && job.id) {
      const cached = jdCache.get(job.id);
      if (cached && String(cached.text || "").trim()) return true;
    }
  } catch (_) { /* node tests / no cache */ }
  if (job.has_description === true) return true;
  return String(job.job_description || "").trim().length > 0;
}

function jobMatchesExtrasFilter(j) {
  if (!extrasFilter) return true;
  if (extrasFilter === "has_url") return !!applicationHref(j);
  if (extrasFilter === "missing_url") return !applicationHref(j);
  if (extrasFilter === "multi_opening") return !!j.multi_opening;
  if (extrasFilter === "multi_source") return jobSourceNames(j).length > 1;
  if (extrasFilter === "no_jd") return j.jd_incomplete === true;
  return true;
}

function jobMatchesSourceFilter(j) {
  if (!sourceFilter) return true;
  const names = jobSourceNames(j);
  if (!names.length) return true;
  if (names.some(n => stampedApproxPrefix(n))) return true;
  const want = sourceFilter.toLowerCase();
  return names.some(n => String(n).toLowerCase() === want) || (j.source || "") === sourceFilter;
}

/** Placeholder hint for fielded search (kept in sync with index.html). */
const SEARCH_PLACEHOLDER = "Search… or company: x jd: y";

const SEARCH_FIELD_ALIAS = {
  jd: "jd",
  description: "jd",
  title: "title",
  company: "company",
  id: "id",
  source: "source",
  location: "location",
  tag: "tag",
  mode: "tag",
};

/** Tokenize search input; quoted segments stay intact (commas literal). */
function tokenizeSearchInput(raw) {
  const s = String(raw || "");
  const out = [];
  let i = 0;
  while (i < s.length) {
    while (i < s.length && /\s/.test(s[i])) i++;
    if (i >= s.length) break;
    if (s[i] === '"') {
      i++;
      let buf = "";
      while (i < s.length && s[i] !== '"') buf += s[i++];
      if (i < s.length && s[i] === '"') i++;
      out.push({ quoted: true, text: buf });
      continue;
    }
    let buf = "";
    while (i < s.length && !/\s/.test(s[i]) && s[i] !== '"') buf += s[i++];
    if (buf) out.push({ quoted: false, text: buf });
  }
  return out;
}

/**
 * Parse list search.
 * - Bare tokens (whitespace): AND across combined haystack. Commas in bare ≈ whitespace (still AND).
 * - field: values — prefixes combinable with AND; comma-separated values within one field = OR.
 * - Optional quotes: company: "foo, bar" is one literal needle.
 */
function parseSearchQuery(raw) {
  const tokens = tokenizeSearchInput(raw);
  const fields = {};
  const bare = [];
  const fieldRe = /^(jd|description|title|company|id|source|location|tag|mode):(.*)$/i;

  const addFieldAlts = (key, alts) => {
    const canon = SEARCH_FIELD_ALIAS[String(key || "").toLowerCase()];
    if (!canon) return;
    if (!fields[canon]) fields[canon] = [];
    for (const alt of alts) {
      const v = String(alt || "").trim().toLowerCase();
      if (v && !fields[canon].includes(v)) fields[canon].push(v);
    }
  };

  const isFieldTok = (tok) => !!(tok && !tok.quoted && fieldRe.test(tok.text));

  const pushUnquotedAlts = (s, alts) => {
    for (const part of String(s || "").split(",")) {
      const v = part.trim();
      if (v) alts.push(v);
    }
  };

  let i = 0;
  while (i < tokens.length) {
    const tok = tokens[i];
    if (!tok.quoted) {
      const m = tok.text.match(fieldRe);
      if (m) {
        const alts = [];
        if (m[2]) pushUnquotedAlts(m[2], alts);
        i++;
        while (i < tokens.length && !isFieldTok(tokens[i])) {
          const n = tokens[i];
          if (n.quoted) alts.push(n.text);
          else pushUnquotedAlts(n.text, alts);
          i++;
        }
        addFieldAlts(m[1], alts);
        continue;
      }
    }
    if (tok.quoted) {
      const v = String(tok.text || "").trim().toLowerCase();
      if (v) bare.push(v);
    } else {
      pushUnquotedAlts(tok.text, bare);
    }
    i++;
  }

  // Dedupe bare while preserving order (pushUnquoted may re-add)
  const bareOut = [];
  for (const t of bare) {
    const v = String(t || "").trim().toLowerCase();
    if (v && !bareOut.includes(v)) bareOut.push(v);
  }
  return { fields, bare: bareOut };
}

/** Slim list fields + chip/tag text (no JD body). */
function jobSearchSlimHaystack(job) {
  if (!job) return "";
  const parts = [
    job.company,
    job.title,
    job.id,
    job.location,
  ];
  try {
    if (typeof jobSourceNames === "function") {
      for (const n of jobSourceNames(job)) parts.push(n);
    }
  } catch (_) { /* tests */ }
  try {
    const wm = typeof jobWorkModeDisplay === "function"
      ? jobWorkModeDisplay(job)
      : { mode: job.work_mode, approx: false };
    if (wm && wm.mode && wm.mode !== "unknown") {
      parts.push(wm.mode);
      if (typeof formatWorkMode === "function") {
        parts.push(formatWorkMode(wm.mode, !!wm.approx));
      }
    }
  } catch (_) { /* ignore */ }
  try {
    const y = typeof jobMinYoeDisplay === "function"
      ? jobMinYoeDisplay(job)
      : { n: job.min_yoe, approx: false };
    if (y && y.n != null && typeof formatYoeLabel === "function") {
      parts.push(formatYoeLabel(y.n, !!y.approx, true));
    }
  } catch (_) { /* ignore */ }
  try {
    const s = typeof jobSalaryDisplay === "function"
      ? jobSalaryDisplay(job)
      : { min: null, max: null, approx: false };
    if (s && (s.min != null || s.max != null || s.display) && typeof formatSalaryLabel === "function") {
      parts.push(formatSalaryLabel(s.min, s.max, {
        approx: !!s.approx, compact: true,
        currency: s.currency || "USD", display: s.display || null, unit: s.unit || null,
      }));
    }
  } catch (_) { /* ignore */ }
  if (job.clearance) parts.push("clearance");
  if (job.us_person) parts.push("us person", "us_person");
  if (job.unresolved_apply_url) parts.push("unresolved url", "unresolved_apply_url");
  if (job.closed_posting || job.closed_posting_label) {
    parts.push("closed posting", "closed_posting", String(job.closed_posting_label || job.deleted_reason || ""));
  }
  if (job.multi_opening) parts.push("multi");
  if (job.jd_incomplete) parts.push("incomplete");
  return parts.filter(Boolean).join(" ").toLowerCase();
}

/** JD text available without a server round-trip (cache and/or preview). */
function jobSearchLocalJd(job) {
  if (!job) return "";
  const parts = [];
  try {
    if (typeof jdCache !== "undefined" && jdCache && job.id) {
      const cached = jdCache.get(job.id);
      if (cached && cached.text) parts.push(String(cached.text));
    }
  } catch (_) { /* node tests */ }
  if (job.job_description) parts.push(String(job.job_description));
  return parts.join("\n").toLowerCase();
}

function searchFieldOrMatch(haystack, alts) {
  if (!alts || !alts.length) return true;
  const h = String(haystack || "");
  return alts.some(a => h.includes(a));
}

function searchTokenInJd(job, token, hits) {
  const t = String(token || "").toLowerCase();
  if (!t) return true;
  if (jobSearchLocalJd(job).includes(t)) return true;
  if (hits && typeof hits.get === "function") {
    const set = hits.get(t);
    if (set && typeof set.has === "function" && job && set.has(job.id)) return true;
  }
  return false;
}

/** Tokens that may need server-side jd_full grep (jd: + bare). */
function searchTokensNeedingJd(parsed) {
  const out = [];
  const add = (t) => {
    const v = String(t || "").trim().toLowerCase();
    if (v && !out.includes(v)) out.push(v);
  };
  if (!parsed) return out;
  for (const t of (parsed.fields && parsed.fields.jd) || []) add(t);
  for (const t of parsed.bare || []) add(t);
  return out;
}

/**
 * Match one job against a parsed query.
 * hits: Map<token, Set<jobId>> from GET /api/jobs/search (optional).
 */
function jobMatchesSearchQuery(job, parsed, hits) {
  if (!parsed) return true;
  const fields = parsed.fields || {};
  const bare = parsed.bare || [];
  if (!Object.keys(fields).length && !bare.length) return true;

  const slim = jobSearchSlimHaystack(job);
  const company = String(job && job.company || "").toLowerCase();
  const title = String(job && job.title || "").toLowerCase();
  const id = String(job && job.id || "").toLowerCase();
  const location = String(job && job.location || "").toLowerCase();
  let source = "";
  try {
    source = (typeof jobSourceNames === "function" ? jobSourceNames(job) : [])
      .join(" ").toLowerCase();
  } catch (_) {
    source = String(job && job.source || "").toLowerCase();
  }

  if (fields.company && !searchFieldOrMatch(company, fields.company)) return false;
  if (fields.title && !searchFieldOrMatch(title, fields.title)) return false;
  if (fields.id && !searchFieldOrMatch(id, fields.id)) return false;
  if (fields.location && !searchFieldOrMatch(location, fields.location)) return false;
  if (fields.source && !searchFieldOrMatch(source, fields.source)) return false;
  if (fields.tag && !searchFieldOrMatch(slim, fields.tag)) return false;
  if (fields.jd) {
    if (!fields.jd.some(t => searchTokenInJd(job, t, hits))) return false;
  }
  for (const t of bare) {
    if (slim.includes(t) || searchTokenInJd(job, t, hits)) continue;
    return false;
  }
  return true;
}

function scheduleJdSearch() {
  const parsed = parseSearchQuery(searchText);
  const tokens = searchTokensNeedingJd(parsed);
  const gen = ++jdSearchGen;
  if (!tokens.length) {
    jdSearchTokenHits = null;
    return;
  }
  const q = tokens.join(" ");
  fetch(`/api/jobs/search?q=${encodeURIComponent(q)}`)
    .then(res => (res.ok ? res.json() : null))
    .then(data => {
      if (gen !== jdSearchGen) return;
      if (!data || !data.hits) return;
      const map = new Map();
      for (const [tok, ids] of Object.entries(data.hits)) {
        map.set(String(tok).toLowerCase(), new Set(Array.isArray(ids) ? ids : []));
      }
      jdSearchTokenHits = map;
      try { render(); } catch (_) { /* boot */ }
    })
    .catch(() => { /* keep prior hits */ });
}

function jobMatchesSearch(j) {
  if (!searchText || !String(searchText).trim()) return true;
  const parsed = parseSearchQuery(searchText);
  return jobMatchesSearchQuery(j, parsed, jdSearchTokenHits);
}

/** Sidebar list filters (not the KPI tab / queue). Shared by list + KPI counts. */
function jobMatchesListFilters(j) {
  return jobMatchesWorkModeFilter(j)
    && jobMatchesYoeFilter(j)
    && jobMatchesDateFilter(j)
    && jobMatchesSalaryFilter(j)
    && jobMatchesExtrasFilter(j)
    && jobMatchesRegion(j)
    && jobMatchesSourceFilter(j)
    && jobMatchesSearch(j);
}

function filterFamilyForBucket(bucket) {
  if (bucket === "applied") return "applied";
  if (bucket === "deleted") return "deleted";
  if (bucket === "open") return "open";
  return "pipeline";
}

/** Saved filters for a family; active family uses live globals (search debounce, etc.). */
function filterStateForFamily(family) {
  if (family === filterFamilyForQueue(queue)) return captureFilterState();
  return filterStateByFamily[family] || {};
}

/** Evaluate list filters from a snapshot without mutating globals. */
function jobMatchesListFiltersForState(state, j) {
  const saved = captureFilterState();
  applyFilterState(state);
  const ok = jobMatchesListFilters(j);
  applyFilterState(saved);
  return ok;
}

function jobMatchesListFiltersForFamily(family, j) {
  return jobMatchesListFiltersForState(filterStateForFamily(family), j);
}

function visibleJobs() {
  let list = jobs.filter(j => {
    if (queue === "deleted") {
      return j.status === "deleted" || LEGACY_SKIPPED_STATUSES.has(j.status);
    }
    if (j.status === "deleted" || LEGACY_SKIPPED_STATUSES.has(j.status)) return false;
    return !isHiddenUntouchedListing(j);
  });
  if (queue !== "deleted") {
    list = list.filter(jobMatchesQueue);
  }
  return list.filter(jobMatchesListFilters);
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

function sortItems(items, key) {
  if (key === "company") items.sort((a, b) => (a.company || "").localeCompare(b.company || ""));
  else if (key === "status") items.sort((a, b) => statusPriorityIndex(a.status) - statusPriorityIndex(b.status));
  else if (key === "yoe") {
    items.sort((a, b) => {
      const ay = jobMinYoeDisplay(a).n;
      const by = jobMinYoeDisplay(b).n;
      const aUnk = ay == null;
      const bUnk = by == null;
      if (aUnk !== bUnk) return aUnk ? 1 : -1;
      if (ay !== by) return ay - by;
      return compareByPosted(a, b);
    });
  } else if (key === "salary" || key === "salary_asc") {
    const asc = key === "salary_asc";
    items.sort((a, b) => {
      const am = jobSalaryDisplay(a).min;
      const bm = jobSalaryDisplay(b).min;
      const aUnk = am == null;
      const bUnk = bm == null;
      if (aUnk !== bUnk) return aUnk ? 1 : -1;
      if (am !== bm) return asc ? am - bm : bm - am;
      return compareByPosted(a, b);
    });
  } else if (key === "multi_opening") {
    items.sort((a, b) => {
      const aM = !!a.multi_opening;
      const bM = !!b.multi_opening;
      if (aM !== bM) return aM ? -1 : 1;
      return compareByPosted(a, b);
    });
  } else {
    items.sort(compareByPosted);
  }
  return items;
}

function populateSourceFilter() {
  const sel = document.getElementById("source-filter");
  if (!sel) return;
  const current = sourceFilter;
  const sources = Array.from(new Set(
    jobs.flatMap(j => jobSourceNames(j)).filter(Boolean)
  )).sort();
  sel.innerHTML = '<option value="">All sources</option>' +
    sources.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
  if (current && !sources.includes(current)) {
    sel.insertAdjacentHTML(
      "beforeend",
      `<option value="${escapeHtml(current)}">${escapeHtml(current)}</option>`
    );
  }
  sel.value = current || "";
}

function toggleGroup(key) {
  if (expandedGroups.has(key)) expandedGroups.delete(key);
  else expandedGroups.add(key);
  render();
}

/** Group key for the current groupBy mode (null when ungrouped). */
function jobGroupKey(job) {
  if (!job || groupBy === "none") return null;
  if (groupBy === "source") return job.source || "(unknown source)";
  return companyKey(job);
}

/**
 * Single source of truth for list tint classes after any render/expand/select.
 * Job row: selected iff id === selectedId.
 * Group header: selected (darker orange) iff expanded OR contains selectedId.
 */
function syncListSelection() {
  const list = document.getElementById("job-list");
  if (!list) return;
  list.querySelectorAll(".job-row[data-id]").forEach(row => {
    row.classList.toggle("selected", row.getAttribute("data-id") === selectedId);
  });
  const selectedKey = selectedId ? jobGroupKey(jobs.find(j => j.id === selectedId)) : null;
  list.querySelectorAll(".job-row.group-header[data-group]").forEach(header => {
    const key = header.getAttribute("data-group");
    header.classList.toggle("selected", expandedGroups.has(key) || (selectedKey != null && key === selectedKey));
  });
}

// datePostedSortKey / compareByPosted are provided by job_sort.js so Posted
// ordering stays consistent, kept in parity with scripts/discovery_filters.py.

function jsStringEscape(s) {
  return String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

/** Escape for double-quoted HTML attribute values (e.g. onclick="..."). */
function escapeAttr(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

/** Private-use sentinels so prune wraps survive escapeHtml + markdown. */
const JD_PRUNE_MARK_OPEN = "\uE000";
const JD_PRUNE_MARK_CLOSE = "\uE001";
/** Search-hit sentinels (distinct from prune so both can nest). */
const SEARCH_MARK_OPEN = "\uE002";
const SEARCH_MARK_CLOSE = "\uE003";

/**
 * Needles for a rendered surface from a parsed query.
 * Fielded prefixes only hit their surface; bare terms hit every surface.
 * Returns lowercase needle strings (not the "company:" / "jd:" prefix).
 */
function searchNeedlesForSurface(parsed, surface) {
  if (!parsed) return [];
  const out = [];
  const add = (alts) => {
    for (const a of alts || []) {
      const v = String(a || "").trim().toLowerCase();
      if (v && !out.includes(v)) out.push(v);
    }
  };
  add(parsed.bare);
  const fields = parsed.fields || {};
  const surf = String(surface || "").toLowerCase();
  if (surf === "company") add(fields.company);
  else if (surf === "title") add(fields.title);
  else if (surf === "jd") add(fields.jd);
  else if (surf === "location") add(fields.location);
  else if (surf === "source") add(fields.source);
  else if (surf === "tag") add(fields.tag);
  else if (surf === "id") add(fields.id);
  return out;
}

/** Active search needles for a surface (empty search → []). */
function activeSearchNeedles(surface) {
  try {
    if (typeof searchText === "undefined" || !String(searchText || "").trim()) return [];
    return searchNeedlesForSurface(parseSearchQuery(searchText), surface);
  } catch (_) {
    return [];
  }
}

/** Case-insensitive substring ranges for needles; merged overlapping spans. */
function collectSearchMatchRanges(text, needles) {
  const s = String(text || "");
  if (!s || !needles || !needles.length) return [];
  const lower = s.toLowerCase();
  const ranges = [];
  for (const needle of needles) {
    const n = String(needle || "").toLowerCase();
    if (!n) continue;
    let from = 0;
    while (from < lower.length) {
      const idx = lower.indexOf(n, from);
      if (idx === -1) break;
      ranges.push([idx, idx + n.length]);
      from = idx + Math.max(1, n.length);
    }
  }
  return mergePruneMatchRanges(ranges);
}

function applySearchHighlightMarks(raw, ranges) {
  let s = String(raw == null ? "" : raw);
  if (!ranges || !ranges.length) return s;
  for (let i = ranges.length - 1; i >= 0; i--) {
    const start = Math.max(0, ranges[i][0]);
    const end = Math.min(s.length, ranges[i][1]);
    if (end <= start) continue;
    s = s.slice(0, start) + SEARCH_MARK_OPEN + s.slice(start, end) + SEARCH_MARK_CLOSE + s.slice(end);
  }
  return s;
}

function finalizeSearchHighlightHtml(html) {
  return String(html)
    .replaceAll(SEARCH_MARK_OPEN, '<mark class="search-hit">')
    .replaceAll(SEARCH_MARK_CLOSE, "</mark>");
}

/**
 * Layered private-use marks on raw text. layers[0] is outermost.
 * Overlap: prune outer + search inner (nested marks) — prune orange stays visible.
 */
function applyLayeredHighlightMarks(raw, layers) {
  const s = String(raw == null ? "" : raw);
  if (!layers || !layers.length) return s;
  const n = s.length;
  const opens = Array.from({ length: n + 1 }, () => []);
  const closes = Array.from({ length: n + 1 }, () => []);
  for (const layer of layers) {
    if (!layer || !layer.ranges || !layer.ranges.length) continue;
    for (const pair of layer.ranges) {
      const start = Math.max(0, pair[0]);
      const end = Math.min(n, pair[1]);
      if (end <= start) continue;
      opens[start].push(layer.open);
      closes[end].push(layer.close);
    }
  }
  let out = "";
  for (let i = 0; i < n; i++) {
    if (closes[i].length) {
      for (let j = closes[i].length - 1; j >= 0; j--) out += closes[i][j];
    }
    for (const o of opens[i]) out += o;
    out += s[i];
  }
  if (closes[n].length) {
    for (let j = closes[n].length - 1; j >= 0; j--) out += closes[n][j];
  }
  return out;
}

/** Escape + green search marks for a list/meta surface (company, title, …). */
function highlightSearchInText(text, surface) {
  const raw = text == null ? "" : String(text);
  if (!raw) return "";
  const needles = activeSearchNeedles(surface);
  if (!needles.length) return escapeHtml(raw);
  const ranges = collectSearchMatchRanges(raw, needles);
  if (!ranges.length) return escapeHtml(raw);
  return finalizeSearchHighlightHtml(escapeHtml(applySearchHighlightMarks(raw, ranges)));
}

function mergePruneMatchRanges(ranges) {
  if (!ranges.length) return [];
  const sorted = ranges.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const out = [[sorted[0][0], sorted[0][1]]];
  for (let i = 1; i < sorted.length; i++) {
    const [start, end] = sorted[i];
    const last = out[out.length - 1];
    if (start <= last[1]) last[1] = Math.max(last[1], end);
    else out.push([start, end]);
  }
  return out;
}

function yoePruneMatchOk(match) {
  const nums = [];
  for (let i = 1; i < match.length; i++) {
    const raw = match[i];
    if (raw == null || raw === "") continue;
    if (!/^\d{1,2}$/.test(String(raw))) continue;
    nums.push(parseInt(raw, 10));
  }
  if (!nums.length) return false;
  return Math.min(...nums) > MAX_ACCEPTABLE_MIN_YOE;
}

function isYoePruneHighlightRegex(rx) {
  return (
    rx === YOE_RANGE_RE ||
    rx === YOE_MIN_PLUS_RE ||
    rx === YOE_YEARS_PLUS_RE ||
    rx === YOE_YEARS_EXPERIENCE_RE ||
    rx === YOE_LABEL_RE ||
    rx === YOE_PLAIN_YEARS_EXP_RE
  );
}

function isYoeTagHighlightRegex(rx) {
  return isYoePruneHighlightRegex(rx) || (
    rx === YOE_FALLBACK_YEARS_OF_WORDS_EXP_RE ||
    rx === YOE_FALLBACK_YEARS_APOS_RE ||
    rx === YOE_FALLBACK_AT_LEAST_RE ||
    rx === YOE_FALLBACK_YEARS_MINIMUM_RE ||
    rx === YOE_FALLBACK_EXP_LABEL_RE ||
    rx === YOE_FALLBACK_YOE_ABBREV_RE ||
    rx === YOE_FALLBACK_RANGE_RE ||
    rx === YOE_FALLBACK_IN_ROLE_RE ||
    rx === YOE_FALLBACK_WORKING_AS_RE ||
    rx === YOE_FALLBACK_YEARS_IN_FIELD_RE
  );
}

function yoeMatchIsEducationEquivalent(blob, start, end) {
  const s = String(blob || "");
  const windowEnd = end == null ? s.length : Math.min(s.length, end + 48);
  if (!/\bequivalent\b/i.test(s.slice(start, windowEnd))) return false;
  return /\bor\s+$/i.test(s.slice(Math.max(0, start - 24), start));
}

function yoeMatchIsSoft(blob, start, end) {
  const s = String(blob || "");
  if (YOE_TENURE_BEFORE_RE.test(s.slice(Math.max(0, start - 64), start))) return true;
  if (YOE_SOFT_BEFORE_RE.test(s.slice(Math.max(0, start - 100), start))) return true;
  if (end != null && YOE_SOFT_AFTER_RE.test(s.slice(end, end + 48))) return true;
  if (yoeMatchIsEducationEquivalent(s, start, end)) return true;
  return false;
}

/** Pay / YOE / work-mode / visa substrings — always-on JD orange, not prune. */
function jdTagHighlightRegexes() {
  return [
    YOE_RANGE_RE,
    YOE_MIN_PLUS_RE,
    YOE_YEARS_PLUS_RE,
    YOE_YEARS_EXPERIENCE_RE,
    YOE_LABEL_RE,
    YOE_PLAIN_YEARS_EXP_RE,
    YOE_FALLBACK_YEARS_OF_WORDS_EXP_RE,
    YOE_FALLBACK_YEARS_APOS_RE,
    YOE_FALLBACK_AT_LEAST_RE,
    YOE_FALLBACK_YEARS_MINIMUM_RE,
    YOE_FALLBACK_EXP_LABEL_RE,
    YOE_FALLBACK_YOE_ABBREV_RE,
    YOE_FALLBACK_RANGE_RE,
    YOE_FALLBACK_IN_ROLE_RE,
    YOE_FALLBACK_WORKING_AS_RE,
    YOE_FALLBACK_YEARS_IN_FIELD_RE,
    WORK_MODE_HYBRID_RE,
    WORK_MODE_REMOTE_RE,
    WORK_MODE_ONSITE_RE,
    WORK_MODE_FALLBACK_HYBRID_RE,
    WORK_MODE_FALLBACK_REMOTE_RE,
    WORK_MODE_FALLBACK_ONSITE_RE,
    CITIZENSHIP_OR_GC_REQUIREMENT_RE,
    NO_VISA_SPONSORSHIP_RE,
    SPONSORS_VISA_RE,
    US_PERSON_REQUIRED_RE,
    JD_VISA_VISIBILITY_RE,
    CLEARANCE_REQUIREMENT_RE,
  ];
}

function salaryRangeHighlightGate(m) {
  const aRaw = m[1];
  const bRaw = m[2];
  const hasCur = /(?:\$|USD)/i.test(aRaw) || /(?:\$|USD)/i.test(bRaw);
  const bothKOrPlain = /(?:[kK]|\d{5,7})/.test(aRaw) && /(?:[kK]|\d{5,7})/.test(bRaw);
  return hasCur || bothKOrPlain;
}

function collectSalaryHighlightRanges(text) {
  const blob = String(text || "");
  if (!blob.trim()) return [];
  const ranges = [];
  const rangeSpans = [];
  const specs = [
    { re: SALARY_LABEL_RE, amounts: (m) => [m[1], m[2]] },
    { re: SALARY_RANGE_RE, amounts: (m) => [m[1], m[2]], gate: salaryRangeHighlightGate },
    { re: SALARY_DOLLAR_SINGLE_RE, amounts: (m) => [m[1], null], skipInSpan: true },
    { re: SALARY_FALLBACK_NEAR_KW_RE, amounts: (m) => [m[1] || m[3], m[2] || m[4]] },
    { re: SALARY_FALLBACK_UP_TO_RE, amounts: (m) => [m[1], null] },
    { re: SALARY_FALLBACK_FROM_RE, amounts: (m) => [m[1], null] },
    { re: SALARY_FALLBACK_BARE_K_RANGE_RE, amounts: (m) => [m[1], m[2]] },
  ];
  for (const { re, amounts, gate, skipInSpan } of specs) {
    if (!re || !re.source) continue;
    const sticky = new RegExp(re.source, `${String(re.flags || "").replace(/[gy]/g, "")}y`);
    for (let i = 0; i < blob.length; i++) {
      sticky.lastIndex = i;
      const match = sticky.exec(blob);
      if (!match || !match[0]) continue;
      const start = i;
      const end = i + match[0].length;
      if (skipInSpan && rangeSpans.some(([rs, rEnd]) => rs <= start && start < rEnd)) continue;
      if (gate && !gate(match)) continue;
      if (salaryIsHourly(blob, start, end)) continue;
      if (salaryIsFundingNoise(blob, start, end)) continue;
      const [aRaw, bRaw] = amounts(match);
      if (!salaryPairFromAmounts(aRaw, bRaw)) continue;
      ranges.push([start, end]);
      if (bRaw) rangeSpans.push([start, end]);
    }
  }
  return mergePruneMatchRanges(ranges);
}

function collectRegexMatchRanges(text, regexes, opts) {
  const s = String(text || "");
  const yoePruneOnly = !!(opts && opts.yoePruneOnly);
  if (!s || !regexes || !regexes.length) return [];
  const ranges = [];
  for (const rx of regexes) {
    if (!rx || !rx.source) continue;
    const sticky = new RegExp(rx.source, `${String(rx.flags || "").replace(/[gy]/g, "")}y`);
    for (let i = 0; i < s.length; i++) {
      sticky.lastIndex = i;
      const match = sticky.exec(s);
      if (!match || !match[0]) continue;
      if (isYoeTagHighlightRegex(rx) && yoeMatchIsSoft(s, i, i + match[0].length)) continue;
      if (yoePruneOnly && isYoePruneHighlightRegex(rx) && !yoePruneMatchOk(match)) continue;
      ranges.push([i, i + match[0].length]);
    }
  }
  return mergePruneMatchRanges(ranges);
}

function collectJdHighlightRanges(text, extraRegexes) {
  return mergePruneMatchRanges([
    ...collectSalaryHighlightRanges(text),
    ...collectRegexMatchRanges(text, jdTagHighlightRegexes(), { yoePruneOnly: false }),
    ...collectPruneMatchRanges(text, extraRegexes),
  ]);
}

/** Map a prune/delete reason code to the discovery regexes that produced it. */
function pruneHighlightRegexesForReason(code) {
  const key = normalizeDeletedReasonCode(code);
  if (key === "citizenship_or_greencard") {
    return [CITIZENSHIP_OR_GC_REQUIREMENT_RE, US_PERSON_REQUIRED_RE];
  }
  if (key === "clearance_or_intel") {
    return [CLEARANCE_REQUIREMENT_RE, INTEL_AGENCY_COMPANY_RE];
  }
  if (key === "excessive_yoe") {
    return [
      YOE_RANGE_RE,
      YOE_MIN_PLUS_RE,
      YOE_YEARS_PLUS_RE,
      YOE_YEARS_EXPERIENCE_RE,
      YOE_LABEL_RE,
      YOE_PLAIN_YEARS_EXP_RE,
    ];
  }
  if (key === "management_track") return [SENIORITY_EXCLUDE_RE];
  if (key === "non_us_location") {
    return [NON_US_LOCATION_RE, INDIA_LOCATION_RE, INDIA_REMOTE_RE];
  }
  return [];
}

function jobPruneHighlightRegexes(job) {
  if (!job || job.status !== "deleted") return [];
  const codes = deletedReasonCodes(job);
  const seen = new Set();
  const out = [];
  for (const code of codes) {
    for (const rx of pruneHighlightRegexesForReason(code)) {
      if (!rx || seen.has(rx)) continue;
      seen.add(rx);
      out.push(rx);
    }
  }
  return out;
}

function collectPruneMatchRanges(text, regexes) {
  return collectRegexMatchRanges(text, regexes, { yoePruneOnly: true });
}

function finalizePruneHighlightHtml(html) {
  return String(html)
    .replaceAll(JD_PRUNE_MARK_OPEN, '<mark class="jd-prune-hit">')
    .replaceAll(JD_PRUNE_MARK_CLOSE, "</mark>");
}

/**
 * Inline **bold** / *italic* on already-classified line text (escaped).
 * searchSurface: field-aware needles (default "jd" for body; identity uses title/company/location).
 */
function formatJdInline(raw, pruneRanges, searchSurface) {
  const surface = searchSurface == null ? "jd" : searchSurface;
  const prune = Array.isArray(pruneRanges) ? pruneRanges : [];
  const searchRanges = collectSearchMatchRanges(raw, activeSearchNeedles(surface));
  const layers = [];
  if (prune.length) {
    layers.push({ open: JD_PRUNE_MARK_OPEN, close: JD_PRUNE_MARK_CLOSE, ranges: prune });
  }
  if (searchRanges.length) {
    layers.push({ open: SEARCH_MARK_OPEN, close: SEARCH_MARK_CLOSE, ranges: searchRanges });
  }
  const source = layers.length
    ? applyLayeredHighlightMarks(raw, layers)
    : (raw == null ? "" : String(raw));
  let s = escapeHtml(source);
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong class="jd-strong">$1</strong>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em class="jd-em">$2</em>');
  return finalizeSearchHighlightHtml(finalizePruneHighlightHtml(s));
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
function formatJobDescriptionHtml(text, pruneRegexes) {
  if (text == null || text === "") return "";
  const extra = Array.isArray(pruneRegexes) ? pruneRegexes : [];
  const inline = (piece) => formatJdInline(
    piece,
    collectJdHighlightRanges(piece, extra),
  );
  const lines = String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const out = [];
  let paraLines = [];
  let bullets = [];

  const flushPara = () => {
    if (!paraLines.length) return;
    const body = paraLines.map(inline).join("<br>");
    out.push(`<p class="jd-p">${body}</p>`);
    paraLines = [];
  };
  const flushList = () => {
    if (!bullets.length) return;
    const items = bullets.map(b => `<li>${inline(b)}</li>`).join("");
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
      out.push(`<div class="jd-heading">${inline(c.text)}</div>`);
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

function setQueue(next) {
  if (next !== queue) swapQueueFilterState(next);
  queue = next;
  if (next === "applied") appliedTableHidden = false;
  if (selectedId) {
    const job = jobs.find(j => j.id === selectedId);
    if (!job || !jobMatchesQueue(job)) selectedId = null;
  }
  document.getElementById("queue-pane")?.classList.toggle("deleted-theme", queue === "deleted");
  const emptyBtn = document.getElementById("empty-deleted-btn");
  if (emptyBtn) emptyBtn.classList.toggle("visible", queue === "deleted");
  const trashBtn = document.getElementById("trash-btn");
  if (trashBtn) trashBtn.classList.toggle("active", queue === "deleted");
  render();
}

function toggleDeletedView() {
  setQueue(queue === "deleted" ? "open" : "deleted");
}

function toggleCompanySiblings(company) {
  const key = normalizeCompanyName(company) || null;
  if (!key) return;
  siblingsPanelCompany = siblingsPanelCompany === key ? null : key;
  renderDossier();
}

function collapseCompanySiblings() {
  siblingsPanelCompany = null;
  renderDossier();
}

function collapseResumePanel() {
  resumePanelJobId = null;
  renderDossier();
}

function snapshotResumeLatexDraft() {
  const editor = document.getElementById("resume-latex-editor");
  if (!editor || !resumeLatexPanelJobId) return;
  const draft = resumeLatexDrafts.get(resumeLatexPanelJobId);
  if (!draft || draft.loading || draft.saving) return;
  const val = editor.value;
  // Poll/re-render can run after fetch fills draft.source but before the
  // textarea is replaced — never copy that empty placeholder over the body.
  if (!draft.dirty && val !== draft.source) return;
  draft.source = val;
}

function closeResumeLatexEditor() {
  snapshotResumeLatexDraft();
  resumeLatexPanelJobId = null;
  renderDossier();
}

async function openResumeLatexEditor(jobId) {
  const job = jobs.find(j => j.id === jobId);
  if (!job) return;
  if (ACTIVE_PROGRESS_STATUSES.has(job.status)) {
    alert("Resume editing is blocked while fill/tailor is running. Cancel first.");
    return;
  }
  resumePanelJobId = null;
  copyKitPanelJobId = null;
  resumeLatexPanelJobId = jobId;
  document.getElementById("resume-wrap")?.classList.remove("open");
  resumeLatexDrafts.set(jobId, {
    source: "",
    isSample: false,
    isWorkspaceMaster: false,
    sourceLabel: "",
    loadedFor: "",
    loading: true,
    saving: false,
    dirty: false,
    error: "",
    status: "Loading resume.tex…",
  });
  renderDossier();
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/resume-latex`);
    const data = await res.json().catch(() => ({}));
    const draft = resumeLatexDrafts.get(jobId);
    if (!draft) return;
    draft.loading = false;
    const latest = jobs.find(j => j.id === jobId) || job;
    const latex = typeof data.latex === "string" ? data.latex : "";
    const missingTex = !!(data.missing_tex || data.ok === false);
    if (!res.ok || missingTex) {
      draft.error = data.error || `Could not load LaTeX (${res.status})`;
      draft.source = latex;
      draft.isSample = false;
      draft.isWorkspaceMaster = false;
      draft.sourceLabel = data.source_label || data.path || "";
      draft.status = draft.error;
      draft.loadedFor = resumePreviewIdentity(latest);
    } else if (!latex.trim() && !data.is_sample) {
      draft.error = data.error || "LaTeX source was empty.";
      draft.source = "";
      draft.status = draft.error;
      draft.loadedFor = resumePreviewIdentity(latest);
    } else {
      draft.source = latex;
      draft.isSample = !!data.is_sample;
      draft.isWorkspaceMaster = !!data.is_workspace_master;
      draft.sourceLabel = data.source_label || data.path || "";
      draft.error = "";
      draft.loadedFor = resumePreviewIdentity(latest);
      if (data.is_sample) {
        draft.status = "Sample starter loaded — replace the dummy details and sections.";
      } else if (data.is_workspace_master) {
        draft.status = "Loaded workspace resume.tex (not a job-specific tailored file).";
      } else {
        draft.status = "Loaded " + (draft.sourceLabel || ("resumes/" + jobId + "/resume.tex"));
      }
    }
  } catch (e) {
    const draft = resumeLatexDrafts.get(jobId);
    if (draft) {
      draft.loading = false;
      draft.error = `Could not load LaTeX: ${e}`;
    }
  }
  if (resumeLatexPanelJobId === jobId && selectedId === jobId) {
    renderDossier();
    requestAnimationFrame(() => document.getElementById("resume-latex-editor")?.focus());
  }
}

async function saveResumeLatex(jobId) {
  snapshotResumeLatexDraft();
  const draft = resumeLatexDrafts.get(jobId);
  if (!draft || draft.loading || draft.saving) return;
  if (!draft.source.trim()) {
    draft.error = "Paste or enter LaTeX before compiling.";
    renderDossier();
    return;
  }
  draft.saving = true;
  draft.error = "";
  draft.status = "Running tectonic and the two-page layout fit…";
  renderDossier();
  try {
    const { data, ok } = await apiPost(
      `/api/jobs/${encodeURIComponent(jobId)}/resume-latex`,
      { latex: draft.source },
      { alertOnError: false },
    );
    if (!ok) {
      draft.error = data.error || "Resume compile failed.";
      draft.status = "Nothing was replaced. Fix the LaTeX and try again.";
    } else {
      if (data.job) {
        const job = jobs.find(j => j.id === jobId);
        if (job) Object.assign(job, data.job);
      }
      draft.source = typeof data.latex === "string" && data.latex ? data.latex : draft.source;
      draft.isSample = false;
      draft.isWorkspaceMaster = false;
      draft.sourceLabel = data.source_label || data.path || ("resumes/" + jobId + "/resume.tex");
      draft.dirty = false;
      draft.error = "";
      draft.loadedFor = resumePreviewIdentity(jobs.find(j => j.id === jobId) || { id: jobId, resume_path: data.resume_path });
      if (data.warning) {
        draft.status = "Saved resume.tex + resume.pdf · fit was best-effort.";
        draft.error = data.warning;
      } else {
        draft.status = "Saved resume.tex + resume.pdf · fitted to two pages.";
      }
      treatResumeOnFile.delete(jobId);
      saveTreatResumeOnFile();
      setSelectedFillMode(jobId, "with-resume");
    }
  } catch (e) {
    draft.error = `Resume compile failed: ${e}`;
    draft.status = "Nothing was replaced. Fix the LaTeX and try again.";
  } finally {
    draft.saving = false;
  }
  if (resumeLatexPanelJobId === jobId && selectedId === jobId) renderDossier();
  await poll();
}

function clearTimelineAutoCollapse() {
  if (_timelineAutoCollapseTimer != null) {
    clearTimeout(_timelineAutoCollapseTimer);
    _timelineAutoCollapseTimer = null;
  }
}

/** True while the pointer or focus is still in the timeline (don't auto-collapse). */
function timelineHasUserAttention() {
  const pane = document.getElementById("timeline-pane");
  if (!pane || timelineCollapsed) return false;
  try {
    return pane.matches(":hover") || pane.matches(":focus-within");
  } catch (_) {
    return false;
  }
}

function scheduleTimelineAutoCollapse() {
  clearTimelineAutoCollapse();
  _timelineAutoCollapseTimer = setTimeout(() => {
    _timelineAutoCollapseTimer = null;
    if (timelineCollapsed) return;
    // Still scrolling / hovering / focusing the list — defer, don't race-collapse.
    if (timelineHasUserAttention()) {
      scheduleTimelineAutoCollapse();
      return;
    }
    setTimelineCollapsed(true);
  }, TL_AUTO_COLLAPSE_MS);
}

function setTimelineCollapsed(collapsed) {
  timelineCollapsed = collapsed;
  try {
    localStorage.setItem(TL_KEY, collapsed ? "1" : "0");
  } catch (_) { /* private mode / storage blocked */ }
  document.getElementById("ops-body")?.classList.toggle("tl-collapsed", collapsed);
  document.getElementById("timeline-pane")?.classList.toggle("collapsed", collapsed);
  const btn = document.getElementById("tl-toggle");
  if (btn) {
    btn.textContent = collapsed ? "▶" : "◀";
    btn.title = collapsed ? "Expand timeline" : "Collapse timeline";
  }
  if (collapsed) {
    clearTimelineAutoCollapse();
  } else {
    scheduleTimelineAutoCollapse();
  }
}

/** True when the event target is inside the timeline pane (or the pane itself). */
function eventInsideTimeline(target) {
  const pane = document.getElementById("timeline-pane");
  if (!pane || !(target instanceof Node)) return false;
  return pane === target || pane.contains(target);
}

/** Re-arm the 10s auto-collapse while the user is still using the timeline. */
function armTimelineAutoCollapseOnInteraction() {
  if (!timelineCollapsed) scheduleTimelineAutoCollapse();
}

function countBucket(bucket, applyFilters = true) {
  const family = filterFamilyForBucket(bucket);
  return jobs.filter(j =>
    j.status !== "deleted"
    && !LEGACY_SKIPPED_STATUSES.has(j.status)
    && !isHiddenUntouchedListing(j)
    && !(bucket === "open" && isNeedsUrlListing(j))
    && queueBucket(j.status) === bucket
    && (!applyFilters || jobMatchesListFiltersForFamily(family, j))
  ).length;
}

function renderStats() {
  const stuck = countBucket("stuck");
  const ready = countBucket("ready");
  const progress = countBucket("progress");
  const open = countBucket("open");
  const applied = TEMP_APPLIED_COUNT_OVERRIDE != null
    ? TEMP_APPLIED_COUNT_OVERRIDE
    : countBucket("applied");
  const deletedN = jobs.filter(j =>
    j.status === "deleted" || LEGACY_SKIPPED_STATUSES.has(j.status)
  ).length;
  const set = (id, n, queueKey, label) => {
    const el = document.getElementById(id);
    if (el) el.textContent = String(n);
    const wrap = document.querySelector(`#mission-stats .mstat[data-queue="${queueKey}"]`);
    if (!wrap) return;
    const familyState = filterStateForFamily(filterFamilyForBucket(queueKey));
    const familyFiltered = filterStateActivity(familyState) > 0;
    if (familyFiltered) {
      const total = countBucket(queueKey, false);
      wrap.title = total !== n ? `${label}: ${n} of ${total} match filters` : label;
    } else {
      wrap.title = label;
    }
  };
  set("stat-stuck", stuck, "stuck", "Stuck / CAPTCHA");
  set("stat-ready", ready, "ready", "Ready for review");
  set("stat-progress", progress, "progress", "In progress");
  set("stat-open", open, "open", "Open / discovered");
  set("stat-applied", applied, "applied", "Applied tracking");

  document.querySelectorAll("#mission-stats .mstat").forEach(el => {
    el.classList.toggle("active", el.getAttribute("data-queue") === queue);
  });

  const trashBadge = document.getElementById("trash-badge");
  if (trashBadge) {
    trashBadge.dataset.n = String(deletedN);
    trashBadge.textContent = deletedN ? String(deletedN) : "";
  }
  const trashBtn = document.getElementById("trash-btn");
  if (trashBtn) trashBtn.classList.toggle("active", queue === "deleted");
  document.getElementById("queue-pane")?.classList.toggle("deleted-theme", queue === "deleted");
  const emptyBtn = document.getElementById("empty-deleted-btn");
  if (emptyBtn) emptyBtn.classList.toggle("visible", queue === "deleted");

  if (lastPollAt) {
    const ago = Math.max(0, Math.floor((Date.now() - lastPollAt) / 1000));
    setSyncState(ago < 10 ? "live" : "stale");
  }
}

/** Header sync dot: green when polling is healthy, amber when stale, red on failure. */
function setSyncState(state) {
  const dot = document.getElementById("sync-dot");
  if (!dot) return;
  dot.classList.toggle("warn", state === "stale");
  dot.classList.toggle("err", state === "error");
  dot.title = state === "error" ? "Sync error" : state === "stale" ? "Sync stale" : "Sync live";
  showConnectionBanner(state === "error");
}

/** Persistent boot/render failure banner (index.html #boot-error-bar). */
function showBootError(message) {
  const bar = document.getElementById("boot-error-bar");
  if (!bar) return;
  if (!message) {
    bar.textContent = "";
    bar.classList.remove("visible");
    return;
  }
  bar.textContent = message;
  bar.classList.add("visible");
}

/** Visible when /api/jobs is unreachable (server down or stale CfT error tab). */
function showConnectionBanner(visible) {
  let bar = document.getElementById("connection-error-bar");
  if (!visible) {
    bar?.remove();
    return;
  }
  if (bar) return;
  bar = document.createElement("div");
  bar.id = "connection-error-bar";
  bar.setAttribute("role", "alert");
  bar.style.cssText = [
    "flex-shrink:0",
    "display:flex",
    "align-items:center",
    "justify-content:space-between",
    "gap:12px",
    "padding:8px 14px",
    "border-bottom:1px solid #5a2020",
    "background:#1a0808",
    "color:#e05555",
    "font-size:11px",
    "letter-spacing:0.03em",
  ].join(";");
  bar.innerHTML = `<span>Dashboard can’t reach the server at <code style="font-family:var(--mono)">${escapeHtml(window.location.host || "127.0.0.1:8787")}</code>. Retrying…</span>`
    + `<button type="button" class="act" id="connection-retry-btn">Retry</button>`;
  const header = document.querySelector(".ops-header");
  if (header && header.parentNode) {
    header.parentNode.insertBefore(bar, header.nextSibling);
  } else {
    document.body.prepend(bar);
  }
  document.getElementById("connection-retry-btn")?.addEventListener("click", () => {
    poll();
    pollStatus();
  });
}

function renderSiblingPanel(job) {
  const key = companyKey(job);
  if (siblingsPanelCompany !== key) return "";
  const siblings = companySiblings(job);
  let body;
  if (!siblings.length) {
    body = `<div class="siblings-empty">No other listings at this company</div>`;
  } else {
    const rows = siblings.map(s => {
      const bucket = queueBucket(s.status);
      const age = postedAgeLabel(s);
      const selected = s.id === selectedId ? " selected" : "";
      const { mode, approx: modeApprox } = jobWorkModeDisplay(s);
      const { n: ymin, approx } = jobMinYoeDisplay(s);
      const modeCell = mode && mode !== "unknown" ? formatWorkMode(mode, modeApprox) : "";
      const yoeCell = ymin != null && !Number.isNaN(ymin) ? formatYoeLabel(ymin, approx) : "";
      return `<tr class="sibling-row${selected}" onclick="${escapeAttr(`selectJob('${jsStringEscape(s.id)}')`)}">
        <td class="sib-title">${escapeHtml(s.title) || "(untitled)"}</td>
        <td><span class="status-pill ${bucket}">${escapeHtml(statusLabel(s.status))}</span></td>
        <td class="sib-muted">${escapeHtml(s.location || "—")}</td>
        <td class="sib-muted">${escapeHtml(modeCell || "—")}</td>
        <td class="sib-muted">${escapeHtml(yoeCell || "—")}</td>
        <td class="sib-muted" title="${escapeHtml(formatDate(jobPostedDisplay(s).iso) || "")}">${escapeHtml(age)}</td>
        <td class="sib-muted">${escapeHtml(s.source || "—")}</td>
      </tr>`;
    }).join("");
    body = `<div class="siblings-scroll">
      <table class="siblings-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Status</th>
            <th>Location</th>
            <th>Mode</th>
            <th>YOE</th>
            <th>Age</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }
  return `<div class="siblings-panel open">
    <div class="siblings-head">
      <span class="micro">Same company · ${siblings.length} other${siblings.length === 1 ? "" : "s"}</span>
      <button type="button" class="siblings-hide" onclick="collapseCompanySiblings()">Hide</button>
    </div>
    ${body}
  </div>`;
}

function renderResumePanel(job) {
  if (!jobHasDiskResume(job) || resumePanelJobId !== job.id) return "";
  const href = `/resume/${encodeURIComponent(job.id)}`;
  const name = resumeDisplayName(job) || "Resume";
  return `<div class="resume-panel">
    <div class="resume-head">
      <span class="micro">${escapeHtml(name)} · full preview</span>
      <span class="resume-head-actions">
        <a class="resume-open-tab" href="${escapeHtml(href)}" target="_blank" rel="noopener">Open in new tab</a>
        <button type="button" class="resume-hide" onclick="collapseResumePanel()">Hide</button>
      </span>
    </div>
    <div class="resume-preview-mount" id="resume-preview-mount"></div>
  </div>`;
}

function renderResumeLatexPanel(job) {
  if (resumeLatexPanelJobId !== job.id) return "";
  const draft = resumeLatexDrafts.get(job.id) || {
    source: "",
    loading: true,
    saving: false,
    isSample: false,
    isWorkspaceMaster: false,
    sourceLabel: "",
    error: "",
    status: "Loading resume.tex…",
  };
  const jid = jsStringEscape(job.id);
  const busy = !!(draft.loading || draft.saving || ACTIVE_PROGRESS_STATUSES.has(job.status));
  const badge = draft.isSample
    ? `<span class="resume-latex-badge">Sample · dummy details</span>`
    : draft.isWorkspaceMaster
      ? `<span class="resume-latex-badge">Workspace resume.tex</span>`
      : "";
  return `<div class="resume-latex-panel">
    <div class="resume-latex-head">
      <div>
        <span class="micro">Resume · LaTeX editor</span>
        ${badge}
      </div>
      <div class="resume-latex-actions">
        <button type="button" class="resume-latex-save"
          ${busy ? "disabled" : ""}
          onclick="${escapeAttr(`saveResumeLatex('${jid}')`)}">${
            draft.saving ? "Fitting & compiling…" : "Fit, recompile & save"
          }</button>
        <button type="button" class="resume-hide" onclick="closeResumeLatexEditor()">Hide</button>
      </div>
    </div>
    <div class="resume-latex-copy">
      Complete <code>.tex</code> source · tectonic compile · layout-only fit to 2 pages.
      Saving replaces this job's available resume only after both steps succeed.
    </div>
    <textarea id="resume-latex-editor" class="resume-latex-editor"
      spellcheck="false" ${draft.loading ? "disabled" : ""}
      placeholder="Paste a complete LaTeX document here…">${escapeHtml(draft.source)}</textarea>
    <div class="resume-latex-foot">
      <span class="resume-latex-status">${escapeHtml(draft.status || "")}</span>
      ${draft.error ? `<pre class="resume-latex-error">${escapeHtml(draft.error)}</pre>` : ""}
    </div>
  </div>`;
}

function copyKitRowByKey(kit, key) {
  if (!kit || !key) return null;
  for (const g of kit.groups || []) {
    for (const row of g.rows || []) {
      if (row.key === key) return row;
    }
    for (const role of g.roles || []) {
      for (const row of role.rows || []) {
        if (row.key === key) return row;
      }
    }
    for (const edu of g.education || []) {
      for (const row of edu.rows || []) {
        if (row.key === key) return row;
      }
    }
  }
  return null;
}

function copyKitAllText(kit) {
  if (!kit) return "";
  const lines = [];
  for (const g of kit.groups || []) {
    if (g.label) lines.push(g.label);
    for (const row of g.rows || []) {
      lines.push(`${row.label}: ${row.value}`);
    }
    for (const role of g.roles || []) {
      const head = [role.company, role.title, role.location, role.period].filter(Boolean).join(" · ");
      if (head) lines.push(head);
      for (const row of role.rows || []) {
        lines.push(`${row.label}: ${row.value}`);
      }
      if (role.bulk_bullets) lines.push(role.bulk_bullets);
      lines.push("");
    }
    for (const edu of g.education || []) {
      const head = [edu.school, edu.degree, edu.period].filter(Boolean).join(" · ");
      if (head) lines.push(head);
      for (const row of edu.rows || []) {
        lines.push(`${row.label}: ${row.value}`);
      }
      lines.push("");
    }
    lines.push("");
  }
  return lines.join("\n").trim();
}

function copyKitRowHtml(jobId, row) {
  if (!row) return "";
  const copied = copyKitCopiedKey === row.key;
  const jid = jsStringEscape(jobId);
  const display = row.value || "";
  return `<button type="button" class="copy-kit-row${copied ? " copied" : ""}"
    title="${escapeAttr("Copy " + (row.label || "value"))}"
    onclick="${escapeAttr(`event.stopPropagation(); copyKitCopyRow('${jid}', '${jsStringEscape(row.key)}')`)}">
    <span class="copy-kit-row-label">${escapeHtml(row.label || "")}</span>
    <span class="copy-kit-row-value">${escapeHtml(display)}</span>
    <span class="copy-kit-row-tick" aria-hidden="true">${copied ? JD_COPIED_ICON_SVG : ""}</span>
  </button>`;
}

function copyKitRoleByIndex(kit, roleIndex) {
  if (!kit || roleIndex == null) return null;
  for (const g of kit.groups || []) {
    const roles = g.roles || [];
    if (g.id === "roles" && roles[roleIndex]) return roles[roleIndex];
  }
  return null;
}

function copyKitBulletsHtml(jobId, role, roleIndex) {
  const bullets = (role.bullets || []).map(b => String(b || "").trim()).filter(Boolean);
  if (!bullets.length) return "";
  const key = `role-${roleIndex}-bullets-all`;
  const copied = copyKitCopiedKey === key;
  const jid = jsStringEscape(jobId);
  const items = bullets.map(b => `<li>${escapeHtml(b)}</li>`).join("");
  return `<button type="button" class="copy-kit-bullets${copied ? " copied" : ""}"
    title="Copy bullets"
    onclick="${escapeAttr(`event.stopPropagation(); copyKitCopyRoleBullets('${jid}', ${roleIndex})`)}">
    <span class="copy-kit-row-label">Bullets</span>
    <ul class="copy-kit-bullets-list">${items}</ul>
    <span class="copy-kit-row-tick" aria-hidden="true">${copied ? JD_COPIED_ICON_SVG : ""}</span>
  </button>`;
}

function renderCopyKitPanel(job) {
  if (copyKitPanelJobId !== job.id) return "";
  // Group labels (CONTACT, ADDRESS, LINKS, RESUME FILE, RESUME ROLES, EDUCATION, …) come from /copy-kit.
  const cached = copyKitCache.get(job.id) || { loading: true, error: "", kit: null };
  const jid = jsStringEscape(job.id);
  const kit = cached.kit;
  const dummy = kit ? !!kit.test_mode : !!testModeEnabled;
  const badge = dummy
    ? `<span class="resume-latex-badge">Dummy · Test Mode</span>`
    : `<span class="resume-latex-badge copy-kit-badge-real">Real profile</span>`;
  let body;
  if (cached.loading && !kit) {
    body = `<div class="copy-kit-status">Loading form kit…</div>`;
  } else if (cached.error && !kit) {
    body = `<div class="copy-kit-status copy-kit-error">${escapeHtml(cached.error)}</div>`;
  } else {
    const groups = (kit && kit.groups) || [];
    if (!groups.length) {
      body = `<div class="copy-kit-status">No copyable values for this job.</div>`;
    } else {
      body = groups.map(g => {
        let rows = (g.rows || []).map(r => copyKitRowHtml(job.id, r)).join("");
        const roles = (g.roles || []).map((role, idx) => {
          const head = [role.company, role.title, role.location, role.period].filter(Boolean).join(" · ") || `Role ${idx + 1}`;
          return `<div class="copy-kit-role">
            <div class="copy-kit-role-head">${escapeHtml(head)}</div>
            ${(role.rows || []).map(r => copyKitRowHtml(job.id, r)).join("")}
            ${copyKitBulletsHtml(job.id, role, idx)}
          </div>`;
        }).join("");
        const education = (g.education || []).map((edu, idx) => {
          const head = [edu.school, edu.degree, edu.period].filter(Boolean).join(" · ") || `Education ${idx + 1}`;
          return `<div class="copy-kit-role">
            <div class="copy-kit-role-head">${escapeHtml(head)}</div>
            ${(edu.rows || []).map(r => copyKitRowHtml(job.id, r)).join("")}
          </div>`;
        }).join("");
        return `<div class="copy-kit-group">
          <div class="copy-kit-group-label">${escapeHtml(g.label || "")}</div>
          ${rows}${roles}${education}
        </div>`;
      }).join("");
    }
  }
  const allBusy = !!(cached.loading && !kit);
  return `<div class="resume-latex-panel copy-kit-panel">
    <div class="resume-latex-head">
      <div>
        <span class="micro">Fast copy · form kit</span>
        ${badge}
      </div>
      <div class="resume-latex-actions">
        <button type="button" class="resume-latex-save" ${allBusy ? "disabled" : ""}
          onclick="${escapeAttr(`copyKitCopyAll('${jid}')`)}">Copy all</button>
        <button type="button" class="resume-hide" onclick="closeCopyKitPanel()" aria-label="Close">✕</button>
      </div>
    </div>
    <div class="resume-latex-copy">Click a row to copy that value, paste into ATS, next row.</div>
    <div class="copy-kit-body">${body}</div>
  </div>`;
}

function closeCopyKitPanel() {
  copyKitPanelJobId = null;
  renderDossier();
}

function toggleCopyKitPanel(jobId) {
  if (!jobId) return;
  if (copyKitPanelJobId === jobId) {
    copyKitPanelJobId = null;
    renderDossier();
    return;
  }
  resumePanelJobId = null;
  resumeLatexPanelJobId = null;
  copyKitPanelJobId = jobId;
  fetchCopyKit(jobId);
}

async function fetchCopyKit(jobId) {
  const prev = copyKitCache.get(jobId);
  copyKitCache.set(jobId, {
    loading: true,
    error: "",
    kit: prev && prev.testMode === testModeEnabled ? prev.kit : null,
    testMode: testModeEnabled,
  });
  if (copyKitPanelJobId === jobId && selectedId === jobId) renderDossier();
  try {
    const res = await fetch(
      `/api/jobs/${encodeURIComponent(jobId)}/copy-kit?test_mode=${testModeEnabled ? "true" : "false"}`
    );
    const data = await res.json().catch(() => ({}));
    const cur = copyKitCache.get(jobId) || {};
    cur.loading = false;
    if (!res.ok) {
      cur.error = data.error || `Could not load form kit (${res.status})`;
    } else {
      cur.kit = data;
      cur.error = "";
      cur.testMode = !!data.test_mode;
    }
    copyKitCache.set(jobId, cur);
  } catch (e) {
    copyKitCache.set(jobId, {
      loading: false,
      error: `Could not load form kit: ${e}`,
      kit: null,
      testMode: testModeEnabled,
    });
  }
  if (copyKitPanelJobId === jobId && selectedId === jobId) renderDossier();
}

async function copyKitCopyRow(jobId, key) {
  const cached = copyKitCache.get(jobId);
  const row = copyKitRowByKey(cached && cached.kit, key);
  if (!row || !row.value) return;
  await copyKitWrite(jobId, key, row.value);
}

async function copyKitCopyRoleBullets(jobId, roleIndex) {
  const cached = copyKitCache.get(jobId);
  const role = copyKitRoleByIndex(cached && cached.kit, roleIndex);
  const text = role && role.bulk_bullets;
  if (!text) return;
  await copyKitWrite(jobId, `role-${roleIndex}-bullets-all`, text);
}

async function copyKitCopyAll(jobId) {
  const cached = copyKitCache.get(jobId);
  const text = copyKitAllText(cached && cached.kit);
  if (!text) return;
  await copyKitWrite(jobId, "__all__", text);
}

async function copyKitWrite(jobId, key, text) {
  const ok = await writeClipboardText(text);
  if (!ok) return;
  if (copyKitCopiedTimer) clearTimeout(copyKitCopiedTimer);
  copyKitCopiedKey = key;
  if (copyKitPanelJobId === jobId && selectedId === jobId) renderDossier();
  copyKitCopiedTimer = setTimeout(() => {
    copyKitCopiedKey = null;
    copyKitCopiedTimer = null;
    if (copyKitPanelJobId === jobId && selectedId === jobId) renderDossier();
  }, 1500);
}

function resumePreviewUrl(jobId) {
  return `/resume/${encodeURIComponent(jobId)}`;
}

function resumePreviewIdentity(job) {
  if (!job || !job.id) return "";
  return `${job.id}::${job.resume_path || ""}`;
}

function canReuseResumePreviewFrame(frame, job) {
  if (!frame || !job || !job.id) return false;
  if (frame.getAttribute("data-job") !== String(job.id)) return false;
  if (frame.getAttribute("data-resume-identity") !== resumePreviewIdentity(job)) return false;
  const have = String(frame.getAttribute("src") || frame.src || "").split("?")[0];
  return have === resumePreviewUrl(job.id);
}

/** Stable slots so poll innerHTML cannot destroy/move the PDF iframe. */
function ensureDossierPreviewShell(root) {
  let main = document.getElementById("dossier-main");
  let host = document.getElementById("resume-preview-host");
  let tail = document.getElementById("dossier-tail");
  if (
    main && host && tail
    && main.parentNode === root
    && host.parentNode === root
    && tail.parentNode === root
  ) {
    return { main, host, tail };
  }
  root.innerHTML = "";
  main = document.createElement("div");
  main.id = "dossier-main";
  host = document.createElement("div");
  host.id = "resume-preview-host";
  tail = document.createElement("div");
  tail.id = "dossier-tail";
  root.appendChild(main);
  root.appendChild(host);
  root.appendChild(tail);
  return { main, host, tail };
}

function paintResumePreview(job, resumeHtml, resumeOpen) {
  const host = document.getElementById("resume-preview-host");
  if (!host) return;
  if (!resumeOpen || !job) {
    if (host.firstChild) host.innerHTML = "";
    return;
  }
  const frame = document.getElementById("resume-preview-frame");
  const mount = document.getElementById("resume-preview-mount");
  if (
    frame && mount
    && host.contains(mount) && mount.contains(frame)
    && canReuseResumePreviewFrame(frame, job)
  ) {
    return;
  }
  host.innerHTML = resumeHtml || "";
  mountResumePreview(job);
}

/** Keep the PDF iframe across dossier re-renders (poll) so Chrome does not flash white. */
function mountResumePreview(job) {
  const mount = document.getElementById("resume-preview-mount");
  if (!mount || !jobHasDiskResume(job) || resumePanelJobId !== job.id) return;
  const src = resumePreviewUrl(job.id);
  const identity = resumePreviewIdentity(job);
  let frame = mount.querySelector("#resume-preview-frame");
  if (frame && canReuseResumePreviewFrame(frame, job)) return;
  if (!frame) {
    frame = document.createElement("iframe");
    frame.id = "resume-preview-frame";
    frame.className = "resume-preview-frame";
    frame.title = `${resumeDisplayName(job) || "Resume"} preview`;
    frame.setAttribute("data-job", job.id);
    frame.setAttribute("data-resume-identity", identity);
    frame.src = src;
    mount.appendChild(frame);
    return;
  }
  frame.setAttribute("data-job", job.id);
  frame.setAttribute("data-resume-identity", identity);
  const have = String(frame.getAttribute("src") || frame.src || "").split("?")[0];
  if (have !== src) frame.src = src;
}

/** Push one list-row chip per visible label (case-insensitive). */
function pushUniqueListTag(tags, seenLabels, label, html) {
  const key = String(label || "").trim().toLowerCase();
  if (!key || seenLabels.has(key)) return false;
  seenLabels.add(key);
  tags.push(html);
  return true;
}

function renderJobRow(job, { nested = false, showCompany = true } = {}) {
  const bucket = queueBucket(job.status);
  const activity = jobActivityDot(job);
  const activityCls = activity ? ` activity-${activity}` : "";
  const outcome = fillOutcome(job);
  const tags = [];
  const seenTagLabels = new Set();
  if (job.multi_opening) {
    pushUniqueListTag(tags, seenTagLabels, "Multi",
      `<span class="tag multi" title="Multiple openings in JD — filter/sort only">Multi</span>`);
  }
  const { mode, approx: modeApprox } = jobWorkModeDisplay(job);
  if (mode && mode !== "unknown") {
    const modeLabel = formatWorkMode(mode, modeApprox);
    pushUniqueListTag(tags, seenTagLabels, modeLabel,
      `<span class="tag work-mode mode-tag ${escapeHtml(mode)}">${highlightSearchInText(modeLabel, "tag")}</span>`);
  }
  const { n: yminTag, approx: yoeApprox } = jobMinYoeDisplay(job);
  if (yminTag != null && !Number.isNaN(Number(yminTag))) {
    const label = formatYoeLabel(yminTag, yoeApprox, true);
    if (label) {
      pushUniqueListTag(tags, seenTagLabels, label,
        `<span class="tag yoe">${highlightSearchInText(label, "tag")}</span>`);
    }
  }
  const { min: salMin, max: salMax, approx: salApprox } = jobSalaryDisplay(job);
  if (salMin != null || salMax != null) {
    const salLabel = formatSalaryLabel(salMin, salMax, {
      approx: salApprox, compact: true,
      currency: (jobSalaryDisplay(job).currency) || "USD",
      display: jobSalaryDisplay(job).display || null,
      unit: jobSalaryDisplay(job).unit || null,
    });
    if (salLabel) {
      pushUniqueListTag(tags, seenTagLabels, salLabel,
        `<span class="tag salary">${highlightSearchInText(salLabel, "tag")}</span>`);
    }
  }
  // Prefer dedicated clearance / US Person chips over deleted-reason text.
  if (job.clearance) {
    pushUniqueListTag(tags, seenTagLabels, "Clearance",
      `<span class="tag clearance" title="Security clearance / eligibility to obtain">${highlightSearchInText("Clearance", "tag")}</span>`);
  }
  if (job.us_person) {
    pushUniqueListTag(tags, seenTagLabels, "US Person",
      `<span class="tag us-person" title="U.S. Person / ITAR / export-controlled">${highlightSearchInText("US Person", "tag")}</span>`);
  }
  if (job.unresolved_apply_url) {
    pushUniqueListTag(tags, seenTagLabels, "Unresolved URL",
      `<span class="tag unresolved-url" title="Apply URL still LinkedIn / aggregator after resolve">${highlightSearchInText("Unresolved URL", "tag")}</span>`);
  }
  if (job.closed_posting || (bucket === "deleted" && /^(dead|closed)\//i.test(String(job.deleted_reason || "")))) {
    const closedLabel = String(job.closed_posting_label || job.deleted_reason || "dead/404").trim() || "dead/404";
    pushUniqueListTag(tags, seenTagLabels, closedLabel,
      `<span class="tag closed-posting" title="Apply/listing URL dead or closed posting">${highlightSearchInText(closedLabel, "tag")}</span>`);
  }
  if (bucket === "deleted") {
    const reasonParts = [];
    for (const code of deletedReasonCodes(job)) {
      const label = deletedReasonLabel(code, { short: true });
      const key = String(label || "").trim().toLowerCase();
      if (!key || seenTagLabels.has(key)) continue;
      seenTagLabels.add(key);
      reasonParts.push(label);
    }
    if (reasonParts.length) {
      const reasonText = reasonParts.join(" · ");
      tags.push(`<span class="tag deleted-reason">${highlightSearchInText(reasonText, "tag")}</span>`);
    }
  }
  const srcChips = sourceChipsHtml(job);
  if (srcChips) tags.push(srcChips);
  const selected = job.id === selectedId ? " selected" : "";
  const nestedCls = nested ? " nested" : "";
  const coHtml = showCompany
    ? (job.company
      ? highlightSearchInText(job.company, "company")
      : escapeHtml("(fetching…)"))
    : "";
  return `<div class="job-row${selected}${nestedCls}" role="button" tabindex="0" data-id="${escapeHtml(job.id)}">
    <div class="status-rail ${bucket}${activityCls}" title="${escapeHtml(statusLabel(job.status))}"></div>
    <div>
      ${showCompany ? `<div class="co">${coHtml}${companyApplyCountBadgeHtml(job)}</div>` : ""}
      <div class="ttl">${highlightSearchInText(job.title || "", "title")}</div>
      ${tags.length ? `<div class="meta-line">${tags.join("")}</div>` : ""}
      ${outcome && (bucket === "stuck" || bucket === "ready")
        ? `<div class="outcome">${escapeHtml(outcome)}</div>` : ""}
    </div>
    <div class="age">${postedAgeLabel(job)}</div>
  </div>`;
}

function renderDeletedReasonGroups(items) {
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
  return entries.map(({ key, items: groupItems, label }) => `
    <div class="job-row reason-header" aria-hidden="true">
      <div class="reason-rail"></div>
      <div>
        <div class="co">${escapeHtml(label)} <span class="count">${groupItems.length}</span></div>
      </div>
      <div class="age"></div>
    </div>
    ${groupItems.map(job => renderJobRow(job, { nested: true })).join("")}
  `).join("");
}

function renderList() {
  const list = document.getElementById("job-list");
  if (!list) return;
  document.getElementById("list-boot-msg")?.remove();
  populateSourceFilter();
  const visible = visibleJobs();
  updateFiltersChrome(visible.length);
  const emptyEl = document.getElementById("filter-empty");
  if (!visible.length) {
    list.innerHTML = "";
    if (emptyEl) {
      emptyEl.classList.add("visible");
      const msg = emptyEl.querySelector("span");
      if (msg) {
        msg.textContent = filtersAreActive()
          ? "No matches — clear filters"
          : (jobs.length ? "No cases in this queue" : "No jobs yet");
      }
      const clearBtn = document.getElementById("clear-filters-btn");
      if (clearBtn) clearBtn.style.display = filtersAreActive() ? "" : "none";
    }
    return;
  }
  if (emptyEl) emptyEl.classList.remove("visible");

  const emptyHtml = "";
  let html = "";

  if (queue === "deleted") {
    html = renderDeletedReasonGroups(visible) || emptyHtml;
  } else if (groupBy === "none") {
    const items = visible.slice();
    sortItems(items, sortBy);
    // In-progress/needs-attention always float to the top within the queue.
    items.sort((a, b) => {
      const aIP = IN_PROGRESS_OR_NEEDS_ATTENTION.includes(a.status);
      const bIP = IN_PROGRESS_OR_NEEDS_ATTENTION.includes(b.status);
      return aIP === bIP ? 0 : (aIP ? -1 : 1);
    });
    html = items.map(job => renderJobRow(job, {})).join("") || emptyHtml;
  } else {
    const groupKeyFn = groupBy === "source"
      ? (j => j.source || "(unknown source)")
      : (j => companyKey(j));
    const groups = new Map();
    for (const j of visible) {
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
    groupEntries.sort((a, b) => {
      if (a.inProgress !== b.inProgress) return a.inProgress ? -1 : 1;
      if (sortBy === "multi_opening") {
        if (a.hasMultiOpening !== b.hasMultiOpening) return a.hasMultiOpening ? -1 : 1;
        return b.sortKey - a.sortKey;
      }
      if (sortBy === "company") return a.key.localeCompare(b.key);
      if (sortBy === "status") {
        return statusPriorityIndex(a.items[0].status) - statusPriorityIndex(b.items[0].status);
      }
      if (sortBy === "yoe") {
        const ay = jobMinYoeDisplay(a.items[0]).n;
        const by = jobMinYoeDisplay(b.items[0]).n;
        const aUnk = ay == null;
        const bUnk = by == null;
        if (aUnk !== bUnk) return aUnk ? 1 : -1;
        if (ay !== by) return ay - by;
        return b.sortKey - a.sortKey;
      }
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

    html = groupEntries.map(({ key, items, priorityStatus, hasMultiOpening }) => {
      if (items.length === 1) {
        return renderJobRow(items[0], {});
      }
      const expanded = expandedGroups.has(key);
      const latest = items[0];
      // Darker orange on group header when expanded, or when it owns the selection (even collapsed).
      const groupActive = expanded || items.some(j => j.id === selectedId);
      const metaParts = groupBy === "source"
        ? [`${items.length} roles`]
        : [latest.source, jobPostedDisplay(latest).iso
            ? `latest ${jobPostedDisplay(latest).approx ? "~" : ""}${formatDate(jobPostedDisplay(latest).iso)}`
            : ""].filter(Boolean);
      if (hasMultiOpening) metaParts.push("multi");
      const activity = jobActivityDot({ status: priorityStatus });
      const activityCls = activity ? ` activity-${activity}` : "";
      const dotColor = STATUS_COLORS[priorityStatus] || "#7a828c";
      return `
        <div class="job-row group-header${groupActive ? " selected" : ""}" role="button" tabindex="0" data-group="${escapeHtml(key)}">
          <div class="expand-icon">${expanded ? "▾" : "▸"}</div>
          <div>
            <div class="co">
              ${!expanded && priorityStatus
                ? `<span class="status-dot${activityCls}"${activity ? "" : ` style="background:${dotColor}"`} title="${escapeHtml(statusLabel(priorityStatus))}"></span>`
                : ""}
              ${escapeHtml(groupBy === "company" ? (latest.company || key) : key) || "(fetching…)"}${groupBy === "company" ? companyApplyCountBadgeHtml(key) : ""} <span class="count">${items.length} roles</span>
            </div>
            ${metaParts.length ? `<div class="group-meta">${metaParts.map(m => escapeHtml(m)).join(" · ")}</div>` : ""}
          </div>
          <div class="age"></div>
        </div>
        ${expanded ? `<div class="group-children">${items.map(job => renderJobRow(job, { nested: true, showCompany: groupBy === "source" })).join("")}</div>` : ""}
      `;
    }).join("") || emptyHtml;
  }

  list.innerHTML = html;

  list.querySelectorAll(".job-row[data-id]").forEach(bindJobListRow);
  list.querySelectorAll(".job-row.group-header[data-group]").forEach(row => {
    const key = row.getAttribute("data-group");
    row.addEventListener("click", () => toggleGroup(key));
    row.addEventListener("keydown", e => {
      if (e.key === "Enter") toggleGroup(key);
    });
  });
  syncListSelection();
  // After first paint: idle-warm JDs for visible/open-queue rows (never blocks /api/jobs).
  scheduleJdCacheWarm();
}

function selectJob(id, opts = {}) {
  selectedId = id;
  const job = jobs.find(j => j.id === id);
  if (job) {
    const key = jobGroupKey(job);
    if (key) expandedGroups.add(key);
    if (siblingsPanelCompany && companyKey(job) !== siblingsPanelCompany) {
      siblingsPanelCompany = null;
    }
    if (resumePanelJobId && resumePanelJobId !== job.id) {
      resumePanelJobId = null;
    }
    if (queue === "applied") {
      // Sidebar pick → dossier-only focus; tracking-table row keeps table visible.
      if (opts.appliedFocus) appliedTableHidden = true;
      // Scroll so action buttons (Fill / Resume / Fast copy) stay in view.
      scrollToAppliedDetail = true;
    }
  }
  activityEvents = synthesizeTimelineFromJob(job);
  rememberJdViewed(id);
  render();
  loadActivity();
  loadJobDescription(id);
  prefetchJdNeighbors(id);
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

function isEasyApplyJob(job) {
  if (!job) return false;
  if (job.easy_apply === true) return true;
  if (job.deleted_reason === "easy_apply") return true;
  if (job.status === "skipped_easy_apply") return true;
  const kind = String(job.apply_kind || "").trim().toLowerCase().replace(/-/g, "_");
  if (kind === "easy_apply") return true;
  const detail = String(job.status_detail || "").toLowerCase();
  if (detail.includes("easy apply") && (job.status === "deleted" || job.status === "skipped_easy_apply")) {
    return true;
  }
  return false;
}

/** True when the visible apply link is still LinkedIn/Indeed/etc. (not resolved to company ATS). */
function applyUrlNeedsResolution(job) {
  if (!job || isEasyApplyJob(job)) return false;
  const href = applicationHref(job);
  if (!href || !isAggregatorHost(href)) return false;
  const res = job.apply_url_resolution;
  if (res && res.confidence === "high" && res.url && !isAggregatorHost(res.url)) return false;
  return true;
}

function applicationHref(job) {
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

function sourceChipsHtml(job) {
  const names = jobSourceNames(job);
  if (!names.length) return "";
  return names.map(n => `<span class="tag source">${highlightSearchInText(n, "source")}</span>`).join("");
}

/** Equality key for apply/alt URLs: lowercased host+path (Ashby org slug twins, etc.). */
function normalizeApplyUrlKey(url) {
  if (!url) return "";
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "").toLowerCase();
    const path = (u.pathname || "").replace(/\/$/, "").toLowerCase();
    // Keep query (indeed jk=, etc.) but drop hash
    return `${u.protocol}//${host}${path}${u.search || ""}`;
  } catch (_) {
    return String(url).replace(/\/$/, "").toLowerCase();
  }
}

function applyUrlHost(url) {
  if (!url) return "";
  try { return new URL(url).hostname.replace(/^www\./, "").toLowerCase(); } catch (_) { return ""; }
}

/** Prefer Ashby /application (and similar) over bare posting URLs when upgrading a chip. */
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
  const seenLabels = new Set(); // one chip per display label
  const coveredHosts = new Set(); // collapse same-host spam (incl. primary ATS)
  if (primaryHost) coveredHosts.add(primaryHost);

  const add = (url, explicitLabel) => {
    if (!url) return;
    const key = normalizeApplyUrlKey(url);
    if (!key || key === primaryKey || seenUrls.has(key)) return;
    const hostname = applyUrlHost(url);
    // Same host as primary or an already-shown chip → skip further URL variants
    if (hostname && coveredHosts.has(hostname)) return;

    const label = (explicitLabel && String(explicitLabel).trim())
      || hostname
      || "link";
    const labelKey = label.toLowerCase();
    if (seenLabels.has(labelKey)) {
      // Same label, different URL we somehow didn't host-collapse: upgrade URL if better
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

const DISCOVER_RADAR_IDLE_SVG = `<svg class="radar-idle" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="12" r="9"/>
  <circle cx="12" cy="12" r="5.5"/>
  <circle cx="12" cy="12" r="1.35" fill="currentColor" stroke="none"/>
</svg>`;
const DISCOVER_RADAR_SVG = `<svg class="radar-sweeping" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="12" r="9"/>
  <circle cx="12" cy="12" r="5.5"/>
  <g class="radar-sweep-arm">
    <path d="M12 12 L12 3 A9 9 0 0 1 18.36 6.36 Z" fill="currentColor" fill-opacity="0.22" stroke="none"/>
    <line x1="12" y1="12" x2="12" y2="3"/>
  </g>
  <circle cx="12" cy="12" r="1.35" fill="currentColor" stroke="none"/>
</svg>`;

async function loadJobDescription(jobId, { background = false } = {}) {
  if (!jobId) return;
  const existing = jdCache.get(jobId);
  if (existing && !existing.loading && (existing.text != null || existing.error)) {
    if (existing.text && !background) {
      if (stampListTagsFromCachedJd(jobId)) refreshJobListRow(jobId);
    }
    if (!background && selectedId === jobId) renderDossier();
    return;
  }
  if (jdInflight.has(jobId)) {
    if (!background && selectedId === jobId) renderDossier();
    return jdInflight.get(jobId);
  }
  // Keep any prior text while fetching so dossier never blanks on re-entry.
  const priorText = (existing && existing.text) || "";
  jdCache.set(jobId, {
    loading: true,
    text: priorText,
    error: null,
    source: (existing && existing.source) || null,
  });
  if (!background && selectedId === jobId) renderDossier();

  const promise = (async () => {
    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/description`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        jdCache.set(jobId, {
          loading: false,
          text: priorText,
          error: data.error || `Failed (${res.status})`,
          source: null,
        });
      } else {
        jdCache.set(jobId, {
          loading: false,
          text: data.job_description || "",
          error: null,
          source: data.source || null,
        });
        if (stampListTagsFromCachedJd(jobId)) refreshJobListRow(jobId);
      }
    } catch (e) {
      jdCache.set(jobId, {
        loading: false,
        text: priorText,
        error: "Failed to load job description",
        source: null,
      });
    } finally {
      jdInflight.delete(jobId);
    }
    if (selectedId === jobId) renderDossier();
  })();
  jdInflight.set(jobId, promise);
  return promise;
}

function rememberJdViewed(jobId) {
  if (!jobId) return;
  const idx = _jdRecentlyViewed.indexOf(jobId);
  if (idx >= 0) _jdRecentlyViewed.splice(idx, 1);
  _jdRecentlyViewed.unshift(jobId);
  while (_jdRecentlyViewed.length > JD_RECENT_MAX) _jdRecentlyViewed.pop();
}

function jdNeedsFetch(jobId) {
  if (!jobId) return false;
  const job = jobs.find(j => j.id === jobId);
  if (!job || !jobHasDescription(job)) return false;
  if (jdInflight.has(jobId)) return false;
  const cached = jdCache.get(jobId);
  if (cached && !cached.loading && (cached.text || cached.error)) return false;
  return true;
}

function scheduleJdCacheWarm() {
  const gen = ++_jdPrefetchGen;
  const kick = () => {
    if (gen !== _jdPrefetchGen) return;
    warmJdCacheForVisible(gen);
  };
  if (_jdPrefetchIdleHandle != null) {
    if (typeof cancelIdleCallback === "function" && typeof _jdPrefetchIdleHandle === "number") {
      try { cancelIdleCallback(_jdPrefetchIdleHandle); } catch (_) { /* ignore */ }
    }
    clearTimeout(_jdPrefetchIdleHandle);
    _jdPrefetchIdleHandle = null;
  }
  if (typeof requestIdleCallback === "function") {
    _jdPrefetchIdleHandle = requestIdleCallback(kick, { timeout: 1200 });
  } else {
    _jdPrefetchIdleHandle = setTimeout(kick, 80);
  }
}

async function warmJdCacheForVisible(gen) {
  const ids = [];
  const push = (id) => {
    if (!id || ids.includes(id)) return;
    ids.push(id);
  };
  push(selectedId);
  for (const id of _jdRecentlyViewed) push(id);
  for (const job of visibleJobs()) {
    if (ids.length >= JD_PREFETCH_MAX) break;
    push(job.id);
  }
  const need = ids.filter(jdNeedsFetch).slice(0, JD_PREFETCH_MAX);
  for (let i = 0; i < need.length; i += JD_PREFETCH_CHUNK) {
    if (gen !== _jdPrefetchGen) return;
    const chunk = need.slice(i, i + JD_PREFETCH_CHUNK);
    await Promise.all(chunk.map(id => loadJobDescription(id, { background: true })));
    await new Promise(r => setTimeout(r, 0));
  }
}

function prefetchJdNeighbors(jobId) {
  const vis = visibleJobs();
  const idx = vis.findIndex(j => j.id === jobId);
  if (idx < 0) return;
  const neighborIds = [];
  for (const off of [-2, -1, 1, 2, 3]) {
    const j = vis[idx + off];
    if (j) neighborIds.push(j.id);
  }
  for (const id of neighborIds) {
    if (jdNeedsFetch(id)) loadJobDescription(id, { background: true });
  }
}

const JD_COPY_ICON_SVG = `<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
  <path fill="currentColor" d="M5.5 1.5A1.5 1.5 0 0 0 4 3v8a1.5 1.5 0 0 0 1.5 1.5h6A1.5 1.5 0 0 0 13 11V5.31a1.5 1.5 0 0 0-.44-1.06l-2.31-2.31a1.5 1.5 0 0 0-1.06-.44H5.5zm0 1.25h3v2.13c0 .48.39.87.87.87h2.38V11a.25.25 0 0 1-.25.25h-6A.25.25 0 0 1 5.25 11V3a.25.25 0 0 1 .25-.25zM3 4.75a.625.625 0 0 0-.625.625V13A1.5 1.5 0 0 0 3.875 14.5H10a.625.625 0 0 0 0-1.25H3.875A.25.25 0 0 1 3.625 13V5.375A.625.625 0 0 0 3 4.75z"/>
</svg>`;

const JD_COPIED_ICON_SVG = `<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
  <path fill="currentColor" d="M13.53 4.22a.75.75 0 0 1 0 1.06l-6.5 6.5a.75.75 0 0 1-1.06 0l-3-3a.75.75 0 1 1 1.06-1.06L6.5 10.19l5.97-5.97a.75.75 0 0 1 1.06 0z"/>
</svg>`;

const MARK_APPLIED_ICON_SVG = `<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
  <path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M3.1 8.15 6.45 11.5 12.9 4.4"/>
</svg>`;

const DELETE_ICON_SVG = `<svg class="icon-trash" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
  <path fill="currentColor" d="M5 1.25h6v1.1H5V1.25zM1.75 3.5h12.5v1.35H1.75V3.5zM3.6 5.9h8.8v7.35A1.65 1.65 0 0 1 10.75 14.9h-5.5A1.65 1.65 0 0 1 3.6 13.25V5.9zm2 1.5v5.1h1.35V7.4H5.6zm3.45 0v5.1H10.4V7.4H9.05z"/>
</svg>`;

const CANCEL_ICON_SVG = `<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
  <circle cx="8" cy="8" r="5.75" fill="none" stroke="currentColor" stroke-width="1.65"/>
  <path fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" d="M4.75 4.75 11.25 11.25"/>
</svg>`;

const APPLY_URL_EDIT_ICON_SVG = `<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
  <path fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round"
    d="M10.85 2.4 13.6 5.15 5.7 13.05 2.75 13.25l.2-2.95 7.9-7.9z"/>
  <path fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" d="M9.55 3.7 12.3 6.45"/>
</svg>`;

/** Icon-only dossier action button (Fast copy, Mark applied, Delete, Cancel). */
function dossierIconBtnHtml({ id, theme, open, title, ariaLabel, onclick, icon }) {
  const openCls = open ? " open" : "";
  return `<button type="button" class="act btn-icon btn-icon-${theme}${openCls}"
    ${id ? `id="${id}"` : ""}
    title="${escapeAttr(title)}"
    aria-label="${escapeAttr(ariaLabel || title)}"
    onclick="${escapeAttr(onclick)}">${icon}</button>`;
}

/** Raw JD source text (same field the renderer consumes), for clipboard copy. */
function jdCopySourceText(job) {
  if (!job) return "";
  const cached = jdCache.get(job.id);
  if (cached && cached.text) return cached.text;
  return job.job_description || "";
}

/** Title/company/location for the JD header, with junk placeholders dropped. */
function jdIdentityFields(job) {
  const clean = (v) => {
    const s = v == null ? "" : String(v).trim();
    if (!s || s === "—" || s === "-") return "";
    if (/^(none|null|undefined|unknown|n\/a|na)$/i.test(s)) return "";
    return s;
  };
  return {
    title: clean(job && job.title),
    company: clean(job && job.company),
    location: clean(job && job.location),
  };
}

function jdIdentityHtml(job) {
  const { title, company, location } = jdIdentityFields(job);
  if (!title && !company && !location) return "";
  const pruneRx = jobPruneHighlightRegexes(job);
  const identInline = (value, surface) => formatJdInline(
    value,
    collectJdHighlightRanges(value, pruneRx),
    surface,
  );
  const subParts = [];
  if (company) subParts.push(identInline(company, "company"));
  if (location) subParts.push(identInline(location, "location"));
  const sub = subParts.join(`<span class="jd-ident-sep">·</span>`);
  return `<div class="jd-ident">
    ${title ? `<div class="jd-ident-title">${identInline(title, "title")}</div>` : ""}
    ${sub ? `<div class="jd-ident-sub">${sub}</div>` : ""}
  </div>`;
}

/** Plain-text identity header prepended to the copied JD. */
function jdIdentityPlainText(job) {
  const { title, company, location } = jdIdentityFields(job);
  const lines = [];
  if (title) lines.push(title);
  const sub = [company, location].filter(Boolean).join(" — ");
  if (sub) lines.push(sub);
  return lines.join("\n");
}

/** Clipboard payload: identity header (when known) then the JD body. */
function jdCopyText(job) {
  const body = jdCopySourceText(job);
  if (!body) return "";
  const head = jdIdentityPlainText(job);
  return head ? `${head}\n\n${body}` : body;
}

function jdCopyButtonHtml(job) {
  if (!jdCopySourceText(job)) return "";
  const copied = jdCopyFlashJobId === job.id;
  const label = copied ? "Job description copied" : "Copy job description";
  return `<button type="button" class="jd-copy-btn${copied ? " copied" : ""}"
    id="jd-copy-btn"
    title="${escapeAttr(label)}" aria-label="${escapeAttr(label)}"
    onclick="event.stopPropagation(); copyJobDescription('${jsStringEscape(job.id)}')">
    ${copied ? JD_COPIED_ICON_SVG : JD_COPY_ICON_SVG}
  </button>`;
}

function jdEditButtonHtml(job) {
  if (!job || !job.id) return "";
  const open = jdEditJobId === job.id;
  return `<button type="button" class="jd-edit-btn${open ? " open" : ""}"
    id="jd-edit-btn"
    title="Edit job description" aria-label="Edit job description"
    onclick="event.stopPropagation(); openJdEditor('${jsStringEscape(job.id)}')">
    ${APPLY_URL_EDIT_ICON_SVG}
  </button>`;
}

function jdToolbarHtml(job) {
  return `<div class="jd-toolbar">${jdCopyButtonHtml(job)}${jdEditButtonHtml(job)}</div>`;
}

function snapshotJdEditDraft() {
  const ta = document.getElementById("jd-edit-textarea");
  if (ta && jdEditJobId) jdEditDraft = ta.value;
}

async function openJdEditor(jobId) {
  const job = jobs.find(j => j.id === jobId);
  if (!job) return;
  let cached = jdCache.get(jobId);
  if (!cached || cached.loading) {
    await loadJobDescription(jobId);
    cached = jdCache.get(jobId);
  }
  jdEditJobId = jobId;
  jdEditDraft = (cached && !cached.error && cached.text) || jdCopySourceText(job) || "";
  renderDossier();
  requestAnimationFrame(() => {
    const ta = document.getElementById("jd-edit-textarea");
    if (ta) {
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }
  });
}

function cancelJdEdit() {
  jdEditJobId = null;
  jdEditDraft = "";
  jdEditSaving = false;
  renderDossier();
}

async function saveJdEdit(jobId) {
  snapshotJdEditDraft();
  if (jdEditSaving) return;
  const ta = document.getElementById("jd-edit-textarea");
  const text = ta ? ta.value : jdEditDraft;
  const saveBtn = document.getElementById("jd-edit-save");
  jdEditSaving = true;
  if (saveBtn) saveBtn.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/jd`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ job_description: text }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "Could not save job description");
    const updated = result.job;
    if (updated && updated.id) {
      const idx = jobs.findIndex(j => j.id === updated.id);
      if (idx >= 0) jobs[idx] = { ...jobs[idx], ...updated };
      lastJobsJSON = JSON.stringify(jobs);
    }
    jdCache.set(jobId, {
      loading: false,
      text: result.job_description != null ? result.job_description : text,
      error: null,
      source: result.source || "jd_full.txt",
    });
    jdEditJobId = null;
    jdEditDraft = "";
    invalidateJobsListCache();
    stampListTagsFromCachedJd(jobId);
    refreshJobListRow(jobId);
    renderDossier();
  } catch (error) {
    alert(error.message || "Could not save job description");
  } finally {
    jdEditSaving = false;
    const btn = document.getElementById("jd-edit-save");
    if (btn) btn.disabled = false;
  }
}

/** Clipboard write with a document.execCommand fallback for non-secure contexts. */
async function writeClipboardText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* fall through to legacy path */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}

async function copyJobDescription(jobId) {
  const job = jobs.find(j => j.id === jobId);
  const text = jdCopyText(job);
  if (!text) return;
  const ok = await writeClipboardText(text);
  if (!ok) return;
  if (jdCopyFlashTimer) clearTimeout(jdCopyFlashTimer);
  jdCopyFlashJobId = jobId;
  const btn = document.getElementById("jd-copy-btn");
  if (btn) {
    btn.classList.add("copied");
    btn.innerHTML = JD_COPIED_ICON_SVG;
    btn.title = "Job description copied";
    btn.setAttribute("aria-label", "Job description copied");
  }
  jdCopyFlashTimer = setTimeout(() => {
    jdCopyFlashJobId = null;
    jdCopyFlashTimer = null;
    if (selectedId === jobId) renderDossier();
  }, 1500);
}

function jdEvidenceHtml(job) {
  if (jdEditJobId === job.id) {
    const jid = jsStringEscape(job.id);
    return `<div class="jd-editor">
      <textarea id="jd-edit-textarea" class="jd-edit-textarea" spellcheck="false"
        aria-label="Edit job description"
        oninput="snapshotJdEditDraft()"
        onkeydown="${escapeAttr(`if (event.key === 'Escape') { event.preventDefault(); cancelJdEdit(); }`)}">${escapeHtml(jdEditDraft)}</textarea>
      <div class="jd-editor-actions">
        <button type="button" class="linkish" id="jd-edit-save"
          ${jdEditSaving ? "disabled" : ""}
          onclick="${escapeAttr(`event.stopPropagation(); saveJdEdit('${jid}')`)}">Save</button>
        <button type="button" class="linkish muted"
          onclick="${escapeAttr(`event.stopPropagation(); cancelJdEdit()`)}">Cancel</button>
      </div>
    </div>`;
  }
  const cached = jdCache.get(job.id);
  const text = (cached && cached.text) || "";
  // Optimistic: show cached/prefetched text immediately (even while a refresh loads).
  if (text) {
    return `<div class="evidence jd-body">${jdIdentityHtml(job)}${formatJobDescriptionHtml(text, jobPruneHighlightRegexes(job))}</div>`;
  }
  const expectJd = job.has_description || (cached && (cached.loading || cached.error));
  if (!expectJd && !cached) {
    return `<div class="evidence jd-empty">No job description on file.</div>`;
  }
  if (cached && cached.error) {
    return `<div class="evidence jd-error">${escapeHtml(cached.error)}</div>`;
  }
  if (!cached || cached.loading) {
    // Short placeholder — prefetch usually fills this before notice is readable.
    return `<div class="evidence jd-loading" aria-busy="true">…</div>`;
  }
  return `<div class="evidence jd-empty">No job description available.</div>`;
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

function jobHasDiskResume(job) {
  // UI-005 / DASH2-005: prefer server disk-truth flag; fall back to path.
  if (!job) return false;
  if (typeof job.resume_on_disk === "boolean") return job.resume_on_disk;
  return !!(job.resume_path);
}

/** Disk PDF, or Test Mode "treat as on file" (skip tailor UX only). */
function jobHasResumeOnFile(job) {
  if (!job) return false;
  if (jobHasDiskResume(job)) return true;
  return testModeEnabled && treatResumeOnFile.has(job.id);
}

function resumeDisplayName(job) {
  if (!job) return "";
  if (job.resume_display_name && jobHasDiskResume(job)) {
    return String(job.resume_display_name);
  }
  const p = job.resume_by_company_path || "";
  if (p && jobHasDiskResume(job)) {
    const base = p.split(/[/\\]/).pop();
    if (base) return base;
  }
  if (testModeEnabled && treatResumeOnFile.has(job.id)) {
    return "Skip PartyRock (no PDF)";
  }
  return "";
}

function defaultFillMode(job) {
  // Test Mode + PartyRock off → always fill-only (never force tailor).
  if (testModeEnabled && !partyRockEnabled) return "with-resume";
  if (jobHasResumeOnFile(job)) return "with-resume";
  return "tailor";
}

function invalidateFillModeDefaults() {
  selectedFillModeByJob.clear();
}

function getSelectedFillMode(job) {
  if (!job) return "tailor";
  const saved = selectedFillModeByJob.get(job.id);
  if (saved === "with-resume" || saved === "tailor") return saved;
  // Migrate removed "retry" → with-resume (UI-009).
  if (saved === "retry") return "with-resume";
  return defaultFillMode(job);
}

function setSelectedFillMode(jobId, mode) {
  if (mode !== "with-resume" && mode !== "tailor") return;
  selectedFillModeByJob.set(jobId, mode);
}

function fillModeLabel(mode) {
  if (mode === "tailor") return "Tailor + fill";
  return "Fill";
}

function fillFaceLabel(job) {
  return fillModeLabel(getSelectedFillMode(job));
}

function pathHintHtml(job) {
  if (jobHasDiskResume(job)) {
    const name = resumeDisplayName(job);
    const fileBit = name ? ` (${escapeHtml(name)})` : "";
    if (testModeEnabled) {
      return `<span class="hint-strong">Resume on disk</span>${fileBit} · Fill only (dummy identity; dummy PDF attach)`;
    }
    return `<span class="hint-strong">Resume on file</span>${fileBit} · Fill will use it for apply`;
  }
  if (testModeEnabled && treatResumeOnFile.has(job.id)) {
    return `<span class="hint-strong">Skip PartyRock (no PDF)</span> · Fill only with dummy resume + DUMMY_PROFILE`;
  }
  if (testModeEnabled && !partyRockEnabled) {
    return `<span class="hint-strong">PartyRock off</span> · Fill only (dummy) — no tailor`;
  }
  if (testModeEnabled && partyRockEnabled) {
    return `<span class="hint-strong">No resume on file</span> · Fill will run PartyRock Testing then dummy fill`;
  }
  return `<span class="hint-strong">No resume on file</span> · Fill will run PartyRock then apply`;
}

function applyResolveLabel(job) {
  if (!job) return "";
  const status = String(job.apply_resolve_status || "").trim();
  if (!status || status === "ok") return "";
  const reason = String(job.apply_resolve_reason || "").trim();
  const human = {
    no_external_apply: "no external apply",
    easy_apply: "Easy Apply only",
    not_logged_in: "not logged in",
    authwall: "not logged in",
    blocked_captcha: "CAPTCHA",
    no_ats_host: "no ATS found",
    unfetchable_ats: "Workday/iCIMS",
    browser_error: "browser error",
    http_error: "HTTP error",
    profile_in_use: "profile in use",
    medium_no_overwrite: "medium confidence",
    not_needed: "not needed",
    low_confidence: "low confidence",
  };
  const short = human[reason] || reason.replace(/_/g, " ") || status;
  if (status === "easy_apply") return `Resolve: ${short}`;
  if (status === "no_external") return `Resolve: ${short}`;
  if (status === "skipped") {
    if (reason === "not_needed") return "";
    return `Resolve: ${short}`;
  }
  if (status === "failed") return `Resolve failed: ${short}`;
  return `Resolve: ${short}`;
}

function applyResolveNoteHtml(job) {
  const label = applyResolveLabel(job);
  if (!label) return "";
  const status = String(job.apply_resolve_status || "").trim();
  const title = String(job.apply_resolve_message || label).trim();
  const cls = status === "failed" ? "apply-resolve-note failed" : "apply-resolve-note";
  return `<span class="${cls}" title="${escapeHtml(title)}">${escapeHtml(label)}</span>`;
}

function idMetaHtml(job, appHref) {
  const parts = [];
  const push = (html) => { if (html) parts.push(html); };
  const sep = `<span class="sep">·</span>`;

  push(escapeHtml(job.company || "(fetching…)") + companyApplyCountBadgeHtml(job));
  if (job.location) push(escapeHtml(job.location));

  const { mode, approx: modeApprox } = jobWorkModeDisplay(job);
  if (mode && mode !== "unknown") {
    push(`<span class="meta-mode">${escapeHtml(formatWorkMode(mode, modeApprox))}</span>`);
  }

  const salInfo = jobSalaryDisplay(job);
  const sal = formatSalaryLabel(salInfo.min, salInfo.max, {
    approx: salInfo.approx, compact: true,
    currency: salInfo.currency || "USD",
    display: salInfo.display || null,
    unit: salInfo.unit || null,
  });
  if (sal) push(`<span class="meta-pay">${escapeHtml(sal)}</span>`);
  const lane = laneForJob(job);
  if (lane === "india" || lane === "worldwide") {
    push(`<span class="meta-lane">${lane === "india" ? "India" : "Worldwide"}</span>`);
  }
  const { n: ymin, approx: yoeApprox } = jobMinYoeDisplay(job);
  if (ymin != null && !Number.isNaN(Number(ymin))) {
    const yoe = formatYoeLabel(ymin, yoeApprox);
    if (yoe) push(`<span class="meta-yoe">${escapeHtml(yoe)}</span>`);
  }

  const { iso: postedIso, approx: postedApprox } = jobPostedDisplay(job);
  const posted = formatDate(postedIso);
  if (posted) push(`Posted ${postedApprox ? "~" : ""}${escapeHtml(posted)}`);

  const updated = formatDate(job.updated_at);
  if (updated) push(`Updated ${escapeHtml(updated)}`);

  let hostHtml = "";
  if (editingApplyUrlId === job.id) {
    const current = job.apply_url || job.job_url || "";
    hostHtml = `<span class="apply-url-editor">
      <input type="url" id="apply-url-input" class="apply-url-input" value="${escapeHtml(current)}"
        placeholder="https://…" autocomplete="off"
        onkeydown="${escapeAttr(`if (event.key === 'Enter') { event.preventDefault(); saveApplyUrl('${jsStringEscape(job.id)}'); } if (event.key === 'Escape') { event.preventDefault(); cancelApplyUrlEditor(); }`)}">
      <button type="button" class="linkish" onclick="${escapeAttr(`event.stopPropagation(); saveApplyUrl('${jsStringEscape(job.id)}')`)}">Save</button>
      <button type="button" class="linkish muted" onclick="${escapeAttr(`event.stopPropagation(); cancelApplyUrlEditor()`)}">Cancel</button>
    </span>`;
  } else if (appHref) {
    let host = appHref;
    try { host = new URL(appHref).hostname.replace(/^www\./, ""); } catch (_) { /* keep */ }
    const unresolved = applyUrlNeedsResolution(job);
    const hostTitle = unresolved
      ? `${appHref} — company ATS apply link not resolved yet (LinkedIn/aggregator)`
      : appHref;
    const hostCls = unresolved ? "meta-host unresolved" : "meta-host";
    hostHtml = `<span class="apply-url-host"><a class="${hostCls}" href="${escapeHtml(appHref)}" target="_blank" rel="noopener" title="${escapeHtml(hostTitle)}">${escapeHtml(host)}</a>${applyUrlEditBtnHtml(job.id, "Edit apply link")}</span>`;
    if (unresolved && job.id) {
      hostHtml += ` <button type="button" class="linkish resolve-apply-btn"
        title="Resolve company ATS apply URL (LinkedIn profile redirect when signed in; never submits, never CAPTCHA)"
        onclick="${escapeAttr(`event.stopPropagation(); resolveApplyUrl('${jsStringEscape(job.id)}')`)}">Resolve ATS</button>`;
    }
  } else if (job.id) {
    hostHtml = `<span class="apply-url-host"><span class="apply-url-placeholder">set apply link</span>${applyUrlEditBtnHtml(job.id, "Set apply link")}</span>`;
  }
  if (hostHtml) push(hostHtml);
  const resolveNote = applyResolveNoteHtml(job);
  if (resolveNote) push(resolveNote);

  return parts.join(sep);
}

function applyUrlEditBtnHtml(jobId, label) {
  const title = label || "Edit apply link";
  return `<button type="button" class="apply-url-edit-btn" title="${escapeAttr(title)}" aria-label="${escapeAttr(title)}"
    onclick="${escapeAttr(`event.stopPropagation(); openApplyUrlEditor('${jsStringEscape(jobId)}')`)}">${APPLY_URL_EDIT_ICON_SVG}</button>`;
}

function dossierSourceChipsHtml(job) {
  const chips = [];
  const seen = new Set();
  const add = (label, url) => {
    const s = String(label || "").trim();
    if (!s) return;
    const key = s.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    chips.push({ label: s, url: url || "" });
  };

  if (Array.isArray(job.sources)) {
    for (const s of job.sources) {
      if (!s) continue;
      add(s.name || s.id, s.apply_url || s.job_url || "");
    }
  }
  for (const l of secondaryApplyLinks(job)) add(l.label, l.url);
  for (const n of jobSourceNames(job)) add(n, "");

  if (!chips.length) return "";
  return `<div class="source-chips">
    <span class="chip-label">Sources</span>
    ${chips.map(c => c.url
      ? `<a class="source-chip" href="${escapeHtml(c.url)}" target="_blank" rel="noopener" title="${escapeHtml(c.url)}">${escapeHtml(c.label)}</a>`
      : `<span class="source-chip dead">${escapeHtml(c.label)}</span>`
    ).join("")}
  </div>`;
}

function renderFillPopover(job) {
  const mode = getSelectedFillMode(job);
  const hasDisk = jobHasDiskResume(job);
  // UI-006: Real Mode without PDF cannot honestly "Fill with resume".
  const withResumeDisabled = !hasDisk && !testModeEnabled;
  const withResumeDesc = withResumeDisabled
    ? "Needs a resume on disk — upload first, or use Tailor + fill"
    : testModeEnabled
      ? "Skip PartyRock — dummy fill only (never attaches tailored PDF in Test Mode)"
      : "Use on-disk resume; skip PartyRock";
  const tailorDesc = testModeEnabled
    ? (partyRockEnabled
      ? "Force PartyRock Testing, then dummy fill (overrides PartyRock off only for this run)"
      : "Forces PartyRock for this run even though header toggle is off")
    : "Regenerate tailored resume, then fill";
  const resumeOnlyBlocked = ACTIVE_PROGRESS_STATUSES.has(job.status)
    || HOLD_BUSY_STATUSES.has(job.status)
    || queueBucket(job.status) === "applied"
    || queueBucket(job.status) === "deleted";
  const jid = jsStringEscape(job.id);
  return `<div class="dossier-popover" id="fill-pop" role="menu">
    <div class="pop-title" data-pin-toggle title="Click to pin/unpin this menu">Fill options · pin</div>
    <label class="opt${withResumeDisabled ? " disabled" : ""}">
      <input type="radio" name="fill-mode" value="with-resume"${mode === "with-resume" && !withResumeDisabled ? " checked" : ""}${withResumeDisabled ? " disabled" : ""}>
      <span>Fill with resume<span class="opt-desc">${withResumeDesc}</span></span>
    </label>
    <label class="opt">
      <input type="radio" name="fill-mode" value="tailor"${mode === "tailor" || withResumeDisabled ? " checked" : ""}>
      <span>Tailor + fill<span class="opt-desc">${tailorDesc}</span></span>
    </label>
    <div class="divider"></div>
    <button type="button" class="pop-btn" ${resumeOnlyBlocked ? "disabled" : ""}
      title="PartyRock + compile PDF, then stop — no form fill"
      onclick="${escapeAttr(`event.stopPropagation(); executeResumeOnly('${jid}')`)}">Generate resume only</button>
    <div class="opt-desc" style="padding:4px 2px 0">PartyRock + compile PDF, then stop — no form fill</div>
  </div>`;
}

function renderResumePopover(job) {
  const onDisk = jobHasDiskResume(job);
  const treatOnly = testModeEnabled && treatResumeOnFile.has(job.id) && !onDisk;
  const onFile = jobHasResumeOnFile(job);
  const name = resumeDisplayName(job);
  const statusCls = onFile ? "on-file" : "missing";
  const statusText = onDisk
    ? (name || "Resume on file")
    : treatOnly
      ? "Skip PartyRock (no PDF)"
      : "No resume on file";
  const jid = jsStringEscape(job.id);
  const midFill = ACTIVE_PROGRESS_STATUSES.has(job.status);
  // UI-011: hide Skip-no-PDF when PartyRock header is already OFF (same outcome).
  const showTreatToggle = testModeEnabled && partyRockEnabled;
  return `<div class="dossier-popover" id="resume-pop" role="menu">
    <div class="pop-title" data-pin-toggle title="Click to pin/unpin this menu">Resume · pin</div>
    <div class="resume-status ${statusCls}" id="resume-status"${onDisk
      ? ` role="button" tabindex="0" title="${escapeAttr(`Preview ${name || "resume"}`)}" onclick="${escapeAttr(`event.stopPropagation(); previewJobResume('${jid}')`)}"`
      : ""}>${escapeHtml(statusText)}</div>
    <div class="pop-actions">
      <label class="pop-btn" style="cursor:pointer${midFill ? ";opacity:.5" : ""}" title="${midFill ? "Blocked while fill/tailor is running" : ""}">
        Upload
        <input type="file" accept=".pdf,.doc,.docx,application/pdf" hidden
          id="resume-upload-input"
          ${midFill ? "disabled" : ""}
          onchange="uploadJobResume('${jid}', this)" />
      </label>
      <button type="button" class="pop-btn" ${midFill ? "disabled" : ""}
        onclick="${escapeAttr(`openResumeLatexEditor('${jid}')`)}">Edit LaTeX</button>
      <button type="button" class="pop-btn danger" id="resume-clear-btn"
        ${onDisk && !midFill ? "" : "disabled"}
        onclick="${escapeAttr(`clearJobResume('${jid}')`)}">Clear</button>
    </div>
    ${showTreatToggle ? `<div class="divider"></div>
    <label class="opt" title="Skip PartyRock without a PDF on disk (persists in this browser)">
      <input type="checkbox" id="resume-on-file-toggle"
        ${treatResumeOnFile.has(job.id) ? "checked" : ""}
        onchange="toggleTreatResumeOnFile('${jid}', this.checked)">
      <span>Skip PartyRock (no PDF)<span class="opt-desc">Persisted · Fill defaults to fill-only (dummy)</span></span>
    </label>` : (testModeEnabled && !partyRockEnabled
      ? `<div class="divider"></div><div class="opt-desc" style="padding:6px 2px;color:var(--text-mute)">PartyRock is off — Fill already skips tailor.</div>`
      : "")}
  </div>`;
}

function renderDossier() {
  snapshotResumeLatexDraft();
  snapshotJdEditDraft();
  const root = document.getElementById("dossier");
  if (!root) return;

  const showAppliedTable = queue === "applied";
  const job = jobs.find(j => j.id === selectedId);
  const showJobDetail = !!(job && (!showAppliedTable || job.status === "applied"));

  if (!showJobDetail) {
    root.innerHTML = showAppliedTable
      ? renderAppliedTableHtml()
      : `<div class="dossier-empty">SELECT A CASE</div>`;
    return;
  }

  const bucket = queueBucket(job.status);
  const otherN = companySiblings(job).length;
  const siblingsOpen = siblingsPanelCompany === companyKey(job);
  if (resumePanelJobId && (!jobHasDiskResume(job) || resumePanelJobId !== job.id)) {
    resumePanelJobId = null;
  }
  if (resumeLatexPanelJobId && resumeLatexPanelJobId !== job.id) {
    resumeLatexPanelJobId = null;
  }
  if (copyKitPanelJobId && copyKitPanelJobId !== job.id) {
    copyKitPanelJobId = null;
  }
  if (jdEditJobId && jdEditJobId !== job.id) {
    jdEditJobId = null;
    jdEditDraft = "";
    jdEditSaving = false;
  }
  const resumeOpen = !!(jobHasDiskResume(job) && resumePanelJobId === job.id);
  const appliedInfo = companyAppliedInfo(job.company);
  const outcome = fillOutcome(job, { full: true });
  const appHref = applicationHref(job);
  const runInProgress = ACTIVE_PROGRESS_STATUSES.has(job.status);
  const canCancel = bucket === "progress" || bucket === "stuck" || bucket === "ready";
  const holdBusySame = HOLD_BUSY_STATUSES.has(job.status);
  const jid = jsStringEscape(job.id);
  // UI-002: never Fill while Ready/CAPTCHA (hold or not — use Mark applied).
  const canFill = !runInProgress && !holdBusySame
    && bucket !== "applied" && bucket !== "deleted";
  const needsRestore = bucket === "deleted";
  const canMarkApplied = bucket !== "applied" && bucket !== "deleted";
  const markAppliedTitle = bucket === "ready"
    ? "Mark applied after you submit on the employer site"
    : runInProgress || STUCK_STATUSES.has(job.status)
      ? "Mark applied — cancels any running fill/tailor for this job"
      : "Shortcut: mark applied even before Ready — confirms first";
  const fillBlockedTitle = holdBusySame
    ? "Ready/CAPTCHA hold — Mark as applied or close the fill browser first"
    : runInProgress
      ? "This job is already running"
      : "Run selected fill option · hover for alternatives · click menu title to pin";

  const appliedAnchorOpen = showAppliedTable && !appliedTableHidden;
  let html = "";
  if (showAppliedTable && !appliedTableHidden) html += renderAppliedTableHtml();
  if (showAppliedTable && appliedTableHidden) {
    html += `<div class="applied-focus-bar">
      <button type="button" class="applied-table-back icon-btn"
        onclick="setAppliedTableHidden(false)"
        title="Show applied tracking table"
        aria-expanded="false">▶ Show applied table</button>
    </div>`;
  }

  const delReasonsLong = bucket === "deleted" ? formatDeletedReasons(job, { short: false }) : null;
  const delReasonsShort = bucket === "deleted" ? formatDeletedReasons(job, { short: true }) : null;
  const statusPillText = bucket === "deleted"
    ? (delReasonsShort ? `Deleted · ${delReasonsShort}` : "Deleted")
    : statusLabel(job.status);

  html += `<header class="id-band"${appliedAnchorOpen ? ' id="job-detail-anchor"' : ""}>
    <div class="id-title-row">
      <h2>${escapeHtml(job.title) || ""}</h2>
      <span class="status-pill ${bucket}">${escapeHtml(statusPillText)}</span>
      ${job.multi_opening ? `<span class="tag multi" title="JD advertises multiple openings — informational tag only; fill/apply path is unchanged">Multi-opening</span>` : ""}
      ${job.unresolved_apply_url ? `<span class="tag unresolved-url" title="Apply URL still LinkedIn / aggregator after resolve">Unresolved URL</span>` : ""}
      ${job.closed_posting || (bucket === "deleted" && /^(dead|closed)\//i.test(String(job.deleted_reason || "")))
        ? `<span class="tag closed-posting" title="Apply/listing URL dead or closed posting">${escapeHtml(String(job.closed_posting_label || job.deleted_reason || "dead/404"))}</span>`
        : ""}
    </div>
    <div class="id-meta">
      ${idMetaHtml(job, appHref)}
      ${appliedInfo ? `<span class="applied-badge">Already applied · ${appliedInfo.count}×${appliedInfo.lastDays != null ? ` · last ${appliedInfo.lastDays}d` : ""}</span>` : ""}
    </div>
    ${dossierSourceChipsHtml(job)}
    ${outcome && (bucket === "stuck" || bucket === "ready")
      ? `<div class="fill-outcome${bucket === "ready" ? " ok" : ""}">${escapeHtml(outcome)}</div>` : ""}
    ${ashbySpamHintHtml(job)}
    ${bucket === "ready"
      ? `<div class="ready-exit-hint">Exit Ready: Mark as applied after you submit on the employer site, or close the fill browser when done reviewing (we never auto-submit). Cancel stops the run and keeps this job in Ready.</div>`
      : ""}
    ${bucket === "deleted" && delReasonsLong
      ? `<div class="id-meta" style="margin-top:6px;margin-bottom:0">Deleted reason · ${escapeHtml(delReasonsLong)}${job.deleted_at ? ` · ${escapeHtml(formatDate(job.deleted_at))}` : ""}</div>`
      : ""}
  </header>`;

  if (job.pending_command) {
    html += `<div class="section command-box">
      <div class="sec-head"><span class="micro">Command approval</span></div>
      <pre class="command">${escapeHtml(job.pending_command)}</pre>
      <div class="btn-row">
        <button class="act primary" type="button" onclick="${escapeAttr(`decideCommand('${jid}', true)`)}">Approve &amp; remember</button>
        <button class="act danger" type="button" onclick="${escapeAttr(`decideCommand('${jid}', false)`)}">Deny</button>
      </div>
    </div>`;
  } else if (STUCK_STATUSES.has(job.status) && job.question) {
    html += `<div class="section command-box">
      <div class="sec-head"><span class="micro">Agent needs help</span></div>
      <div style="margin-bottom:8px">${escapeHtml(job.question)}</div>
      <textarea id="answer" rows="3" placeholder="Type your answer..."></textarea>
      <div class="btn-row"><button class="act primary" type="button" onclick="${escapeAttr(`submitAnswer('${jid}')`)}">Send answer</button></div>
    </div>`;
  }

  const fillLabel = fillFaceLabel(job);
  const resumeOn = jobHasResumeOnFile(job);
  const resumeColorCls = resumeOn ? "resume-on" : "resume-off";
  html += `<section class="path-band">
    <div class="path-mode-tag">${testModeEnabled ? "test · dummy" : "real profile"}</div>
    <div class="path-hint" id="path-hint">${pathHintHtml(job)}</div>
    <div class="actions-row">
      ${needsRestore
        ? `<button class="act primary" type="button" onclick="${escapeAttr(`restoreJob('${jid}')`)}">Restore</button>`
        : `<div class="hover-pop" id="fill-wrap">
            <button type="button" class="act primary" id="fill-btn"
              ${canFill ? "" : "disabled"}
              title="${escapeAttr(fillBlockedTitle)}"
              onclick="${escapeAttr(`event.stopPropagation(); executeFillFace('${jid}')`)}">
              <span id="fill-label">${escapeHtml(fillLabel)}</span>
            </button>
            <button type="button" class="act fill-menu-btn" id="fill-menu-btn"
              ${canFill ? "" : "disabled"}
              aria-label="Fill options" aria-haspopup="menu"
              title="Fill options — including generate resume only"
              onclick="toggleFillMenu(event)"><span aria-hidden="true">▾</span></button>
            ${renderFillPopover(job)}
          </div>`}
      <div class="hover-pop" id="resume-wrap">
        <button type="button" class="act ${resumeColorCls}" id="resume-btn"
          title="${resumeOn ? (jobHasDiskResume(job) ? `Preview ${escapeAttr(resumeDisplayName(job) || "resume")}` : "Skip PartyRock (no PDF)") : "No resume · click to upload"}"
          onclick="${escapeAttr(`event.stopPropagation(); executeResumeFace('${jid}')`)}">Resume</button>
        <button type="button" class="act resume-menu-btn" id="resume-menu-btn"
          aria-label="Resume options" aria-haspopup="menu"
          title="Upload, edit LaTeX, or clear resume"
          onclick="toggleResumeMenu(event)"><span aria-hidden="true">▾</span></button>
        ${renderResumePopover(job)}
      </div>
      ${dossierIconBtnHtml({
        id: "copy-kit-btn",
        theme: "fastcopy",
        open: copyKitPanelJobId === job.id,
        title: "Fast copy",
        ariaLabel: "Fast copy",
        onclick: `event.stopPropagation(); toggleCopyKitPanel('${jid}')`,
        icon: JD_COPY_ICON_SVG,
      })}
      ${canMarkApplied
        ? dossierIconBtnHtml({
          theme: "applied",
          title: markAppliedTitle,
          ariaLabel: "Mark as applied",
          onclick: `markSubmitted('${jid}')`,
          icon: MARK_APPLIED_ICON_SVG,
        })
        : ""}
      ${canCancel
        ? dossierIconBtnHtml({
          theme: "danger",
          title: "Cancel",
          ariaLabel: "Cancel",
          onclick: `cancelJob('${jid}')`,
          icon: CANCEL_ICON_SVG,
        })
        : ""}
      ${dossierIconBtnHtml({
        theme: "danger",
        title: bucket === "applied"
          ? "Soft-delete an applied record (asks for confirmation)"
          : "Move to Deleted",
        ariaLabel: "Delete",
        onclick: `deleteJob('${jid}')`,
        icon: DELETE_ICON_SVG,
      })}
    </div>
    ${renderCopyKitPanel(job)}
    ${renderResumeLatexPanel(job)}
    ${otherN > 0
      ? `<div class="same-co-link">
          <button type="button" class="linkish"
            onclick="${escapeAttr(`toggleCompanySiblings('${jsStringEscape(job.company || "")}')`)}"
            aria-expanded="${siblingsOpen ? "true" : "false"}">
            ${otherN} other role${otherN === 1 ? "" : "s"} at ${escapeHtml(job.company || "company")} →
          </button>
        </div>`
      : `<div class="same-co-link muted">No other roles at ${escapeHtml(job.company || "this company")}.</div>`}
    ${renderSiblingPanel(job)}
  </section>`;

  let afterHtml = `<div class="section" style="border-bottom:0">
    <div class="sec-head">
      <span class="micro">Evidence · JD</span>
      <div class="sec-head-right">
        ${jdToolbarHtml(job)}
      </div>
    </div>
    ${jdEvidenceHtml(job)}
  </div>`;

  if (job.qa_log && job.qa_log.length) {
    afterHtml += `<div class="section"><div class="sec-head"><span class="micro">Q&amp;A history</span></div><div class="qa-log">`;
    for (const qa of job.qa_log) {
      afterHtml += `<div class="item"><div class="q">${escapeHtml(qa.question || "")}</div><div class="a">→ ${escapeHtml(qa.answer)}</div></div>`;
    }
    afterHtml += `</div></div>`;
  }

  const { main, tail } = ensureDossierPreviewShell(root);
  main.innerHTML = html;
  tail.innerHTML = afterHtml;
  paintResumePreview(job, renderResumePanel(job), resumeOpen);

  const latexEditor = document.getElementById("resume-latex-editor");
  if (latexEditor) {
    latexEditor.addEventListener("input", () => {
      const draft = resumeLatexDrafts.get(job.id);
      if (!draft) return;
      draft.source = latexEditor.value;
      draft.dirty = true;
      draft.error = "";
    });
  }

  const latexDraft = resumeLatexDrafts.get(job.id);
  const resumeIdent = resumePreviewIdentity(job);
  if (
    resumeLatexPanelJobId === job.id
    && !ACTIVE_PROGRESS_STATUSES.has(job.status)
    && latexDraft
    && !latexDraft.loading
    && !latexDraft.saving
    && !latexDraft.dirty
    && latexDraft.loadedFor
    && latexDraft.loadedFor !== resumeIdent
  ) {
    queueMicrotask(() => {
      if (resumeLatexPanelJobId === job.id) openResumeLatexEditor(job.id);
    });
  }

  bindDossierPopoverHandlers(job);

  if (scrollToAppliedDetail) {
    scrollToAppliedDetail = false;
    requestAnimationFrame(() => {
      const el = document.getElementById("job-detail-anchor")
        || document.querySelector(".path-band")
        || document.getElementById("dossier");
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
}

function bindDossierPopoverHandlers(job) {
  const fillPop = document.getElementById("fill-pop");
  if (fillPop) {
    fillPop.querySelectorAll("label.opt").forEach(label => {
      label.addEventListener("click", e => {
        e.preventDefault();
        e.stopPropagation();
        const input = label.querySelector('input[name="fill-mode"]');
        if (!input) return;
        input.checked = true;
        setSelectedFillMode(job.id, input.value);
        const face = document.getElementById("fill-label");
        if (face) face.textContent = fillModeLabel(input.value);
      });
    });
  }
  // UI-032: click-pin Fill/Resume menus (like Filters .open).
  for (const wrapId of ["fill-wrap", "resume-wrap"]) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) continue;
    wrap.querySelector("[data-pin-toggle]")?.addEventListener("click", e => {
      e.preventDefault();
      e.stopPropagation();
      wrap.classList.toggle("open");
    });
    wrap.querySelector(".dossier-popover")?.addEventListener("mousedown", e => {
      e.stopPropagation();
    });
  }
}

const _fillFaceBusyJobs = new Set();

function executeFillFace(jobId) {
  if (_fillFaceBusyJobs.has(jobId)) return;
  const job = jobs.find(j => j.id === jobId);
  if (!job) return;
  if (ACTIVE_PROGRESS_STATUSES.has(job.status) || HOLD_BUSY_STATUSES.has(job.status)) {
    alert("Fill blocked — this job is already running or held Ready/CAPTCHA.");
    return;
  }
  const mode = getSelectedFillMode(job);
  _fillFaceBusyJobs.add(jobId);
  Promise.resolve(startJobFillMode(jobId, mode)).finally(() => {
    _fillFaceBusyJobs.delete(jobId);
  });
}

function executeResumeFace(jobId) {
  const job = jobs.find(j => j.id === jobId);
  if (!job) return;
  // Preview is read-only — never block while fill/tailor is running.
  if (jobHasDiskResume(job)) {
    previewJobResume(jobId);
    return;
  }
  if (ACTIVE_PROGRESS_STATUSES.has(job.status)) {
    alert("Resume upload/clear blocked while fill/tailor is running. Cancel first.");
    return;
  }
  // No PDF — open file picker (hover menu also offers upload / treat-as-on-file).
  document.getElementById("resume-upload-input")?.click();
}

function toggleFillMenu(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  document.getElementById("fill-wrap")?.classList.toggle("open");
}

function toggleResumeMenu(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  document.getElementById("resume-wrap")?.classList.toggle("open");
}

function executeResumeOnly(jobId) {
  if (_fillFaceBusyJobs.has(jobId)) return;
  const job = jobs.find(j => j.id === jobId);
  if (!job) return;
  if (ACTIVE_PROGRESS_STATUSES.has(job.status) || HOLD_BUSY_STATUSES.has(job.status)) {
    alert("Generate resume blocked — this job is already running or held Ready/CAPTCHA.");
    return;
  }
  document.getElementById("fill-wrap")?.classList.remove("open");
  _fillFaceBusyJobs.add(jobId);
  Promise.resolve(startJobFillMode(jobId, "resume-only")).finally(() => {
    _fillFaceBusyJobs.delete(jobId);
  });
}

function toggleTreatResumeOnFile(jobId, on) {
  if (!testModeEnabled) {
    treatResumeOnFile.delete(jobId);
    saveTreatResumeOnFile();
    renderDossier();
    return;
  }
  if (on) {
    treatResumeOnFile.add(jobId);
    setSelectedFillMode(jobId, "with-resume");
  } else {
    treatResumeOnFile.delete(jobId);
    const job = jobs.find(j => j.id === jobId);
    if (!jobHasDiskResume(job) && selectedFillModeByJob.get(jobId) === "with-resume") {
      selectedFillModeByJob.delete(jobId);
    }
  }
  saveTreatResumeOnFile();
  renderDossier();
}

function previewJobResume(jobId) {
  const job = jobs.find(j => j.id === jobId);
  if (!jobHasDiskResume(job)) return;
  resumeLatexPanelJobId = null;
  copyKitPanelJobId = null;
  resumePanelJobId = jobId;
  renderDossier();
}

async function clearJobResume(jobId) {
  const job = jobs.find(j => j.id === jobId);
  if (job && ACTIVE_PROGRESS_STATUSES.has(job.status)) {
    alert("Clear resume blocked while fill/tailor is running. Cancel first.");
    return;
  }
  if (!confirm("Clear resume on file for this job?")) return;
  treatResumeOnFile.delete(jobId);
  saveTreatResumeOnFile();
  await apiPost(`/api/jobs/${encodeURIComponent(jobId)}/resume`, undefined, { method: "DELETE", failLabel: "Clear resume" });
  resumePanelJobId = null;
  await poll();
}

async function startJobFillMode(jobId, mode) {
  // with-resume → skip PartyRock only when resume is usable (on disk) or
  // Test Mode can bypass (dummy fill). Real mode without PDF must not claim
  // skip — use Tailor instead (UI-006).
  // tailor → force PartyRock even if a resume is already on disk
  // resume-only → PartyRock + compile/publish, then stop (no form fill)
  let normalized = mode === "tailor" || mode === "with-resume" || mode === "resume-only"
    ? mode
    : "tailor";
  if (normalized === "retry") normalized = "with-resume";
  const job = jobs.find(j => j.id === jobId);
  if (normalized === "resume-only") {
    await startJob(jobId, {
      skipPartyrock: false,
      forcePartyrock: true,
      resumeOnly: true,
    });
    return;
  }
  if (normalized === "with-resume" && !jobHasDiskResume(job) && !testModeEnabled) {
    alert("No resume on disk — upload a PDF or choose Tailor + fill.");
    return;
  }
  setSelectedFillMode(jobId, normalized);
  const forcePartyrock = normalized === "tailor";
  let skipPartyrock = false;
  if (!forcePartyrock && normalized === "with-resume") {
    skipPartyrock = !!(jobHasDiskResume(job) || testModeEnabled);
  }
  await startJob(jobId, { skipPartyrock, forcePartyrock });
}

function timelineKind(ev) {
  const e = ((ev.event || "") + " " + (ev.detail || "")).toLowerCase();
  if (/error|fail|captcha|abort|denied|blocked/.test(e)) return "err";
  if (/warn|stuck|retry|waiting|reconstructed/.test(e)) return "warn";
  if (/complete|ready|ok|success|uploaded|submitted|approved|applied|discovered|added/.test(e)) return "ok";
  return "";
}

/** Instant lifecycle sketch from job fields while /activity loads (mirrors server synth). */
function synthesizeTimelineFromJob(job) {
  if (!job) return [];
  const events = [];
  const created = job.created_at || "";
  const updated = job.updated_at || "";
  const status = (job.status || "").trim();
  const detail = (job.status_detail || "").trim();
  const source = (job.source || "").trim();
  const clockFromIso = (iso) => {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "—";
      return d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch (_) {
      return "—";
    }
  };
  if (created) {
    events.push({
      time: clockFromIso(created),
      event: source === "manual" ? "added" : "discovered",
      detail: source === "manual"
        ? "Added manually via dashboard."
        : (source ? `Discovered via ${source}.` : "Discovered."),
      reconstructed: true,
    });
  }
  if (job.resume_path && ["ready_for_review", "applied", "filling", "navigating", "tailoring", "resume_ready", "stuck", "blocked_captcha"].includes(status)) {
    events.push({
      time: "—",
      event: "resume",
      detail: "Resume on file (reconstructed — exact ready/fill start time was not stored).",
      reconstructed: true,
    });
  }
  if (status && status !== "discovered" && updated) {
    events.push({
      time: clockFromIso(updated),
      event: status,
      detail: detail || `Status → ${status}`,
      reconstructed: true,
    });
  }
  return events;
}

function renderTimeline() {
  const root = document.getElementById("timeline");
  if (!root) return;
  root.innerHTML = "";
  if (!selectedId) {
    root.innerHTML = `<div class="dossier-empty" style="padding:16px;height:auto;font-size:10px;">NO CASE</div>`;
    return;
  }
  let events = activityEvents;
  if (!events.length) {
    const job = jobs.find(j => j.id === selectedId);
    events = synthesizeTimelineFromJob(job);
  }
  if (!events.length) {
    root.innerHTML = `<div class="dossier-empty" style="padding:16px;height:auto;font-size:10px;">NO ACTIVITY YET</div>`;
    return;
  }
  const ordered = events.slice().reverse();
  root.innerHTML = ordered.map(ev => {
    const kind = timelineKind(ev);
    const label = ev.reconstructed
      ? `${escapeHtml(ev.event || "")} <span class="micro" style="opacity:.55">(reconstructed)</span>`
      : escapeHtml(ev.event || "");
    return `<div class="tl-item">
      <div class="t">${escapeHtml(ev.time || "")}</div>
      <div class="rail"><div class="node ${kind}"></div></div>
      <div class="body">
        <div class="ev">${label}</div>
        ${ev.detail ? `<div class="detail">${escapeHtml(ev.detail)}</div>` : ""}
      </div>
    </div>`;
  }).join("");
}

function render() {
  try {
    rebuildCompanyAppliedCounts();
    renderStats();
    renderList();
    renderDossier();
    renderTimeline();
    showBootError("");
  } catch (err) {
    console.error("render failed", err);
    showBootError(`Dashboard render failed (${err}). Hard-refresh or restart the server.`);
    setSyncState("error");
  }
}

function bindOpsChrome() {
  document.getElementById("mission-stats")?.addEventListener("click", e => {
    const m = e.target.closest(".mstat");
    if (!m) return;
    const q = m.getAttribute("data-queue");
    if (q) setQueue(q);
  });

  let searchTimer;
  const searchEl = document.getElementById("search");
  if (searchEl) {
    if (typeof SEARCH_PLACEHOLDER === "string" && SEARCH_PLACEHOLDER) {
      searchEl.placeholder = SEARCH_PLACEHOLDER;
    }
    searchEl.value = searchText;
    searchEl.addEventListener("input", e => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        searchText = e.target.value;
        saveFilterState();
        updateFiltersChrome();
        render();
        scheduleJdSearch();
      }, 180);
    });
    searchEl.addEventListener("keydown", e => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      if (!searchEl.value) return;
      searchEl.value = "";
      searchText = "";
      jdSearchTokenHits = null;
      jdSearchGen++;
      saveFilterState();
      updateFiltersChrome();
      render();
    });
  }

  const onFilterChange = (id, apply) => {
    document.getElementById(id)?.addEventListener("change", e => {
      apply(e.target.value || "");
      saveFilterState();
      updateFiltersChrome();
      render();
    });
  };
  onFilterChange("source-filter", v => { sourceFilter = v; });
  onFilterChange("group-by", v => { groupBy = v || "none"; });
  onFilterChange("sort-by", v => { sortBy = v || "date"; });
  onFilterChange("work-mode-filter", v => { workModeFilter = v; });
  onFilterChange("yoe-filter", v => { yoeFilter = v; });
  onFilterChange("date-filter", v => { dateFilter = v; });
  onFilterChange("salary-filter", v => { salaryFilter = v; });
  onFilterChange("extras-filter", v => { extrasFilter = v; });
  onFilterChange("region-filter", v => { regionFilter = v; });

  syncFilterControlsFromState();
  updateFiltersChrome();
  scheduleJdSearch();

  // Filters: click toggles; optional ~2/3s hover opens (not instant CSS :hover).
  // Always closes on mouseleave of #list-filters (toggle + panel + hit bridge),
  // plus outside click / Escape.
  document.getElementById("filters-toggle")?.addEventListener("click", e => {
    e.stopPropagation();
    const wrap = document.getElementById("list-filters");
    setFiltersPopoverOpen(!wrap?.classList.contains("open"));
  });
  const filtersWrap = document.getElementById("list-filters");
  filtersWrap?.addEventListener("mouseenter", () => {
    if (filtersWrap.classList.contains("open")) return;
    clearFiltersHoverOpenTimer();
    filtersHoverOpenTimer = setTimeout(() => {
      filtersHoverOpenTimer = null;
      if (!filtersWrap.classList.contains("open")) {
        setFiltersPopoverOpen(true);
      }
    }, FILTERS_HOVER_OPEN_MS);
  });
  filtersWrap?.addEventListener("mouseleave", () => {
    clearFiltersHoverOpenTimer();
    if (filtersWrap.classList.contains("open")) {
      setFiltersPopoverOpen(false);
    }
  });
  document.getElementById("filters-popover")?.addEventListener("click", e => {
    e.stopPropagation();
  });

  const clearFilters = () => clearListFilters();
  document.getElementById("clear-filters-btn")?.addEventListener("click", clearFilters);
  document.getElementById("filters-popover-clear")?.addEventListener("click", clearFilters);
  document.getElementById("filters-today")?.addEventListener("click", applyTodayFilterPreset);

  // Hover/focus-within shows add-URL panel; click pins for touch/keyboard.
  document.getElementById("add-job-btn")?.addEventListener("click", e => {
    e.stopPropagation();
    const wrap = document.getElementById("add-job-wrap");
    setAddJobPopoverOpen(!wrap?.classList.contains("open"));
  });
  document.getElementById("add-job-wrap")?.addEventListener("mouseenter", () => {
    // Focus URL on hover-open so paste works immediately — but never steal
    // focus from Search (or other non-add controls) while the user is typing.
    const input = document.getElementById("add-job-url");
    const active = document.activeElement;
    if (!input || active === input) return;
    if (active && active !== document.body && active !== document.documentElement) {
      const wrap = document.getElementById("add-job-wrap");
      if (!wrap || !wrap.contains(active)) return;
    }
    requestAnimationFrame(() => {
      if (document.activeElement === document.getElementById("search")) return;
      input.focus();
    });
  });
  document.getElementById("add-job-popover")?.addEventListener("click", e => {
    e.stopPropagation();
  });

  document.addEventListener("mousedown", e => {
    const filters = document.getElementById("list-filters");
    if (filters?.classList.contains("open") && !filters.contains(e.target)) {
      setFiltersPopoverOpen(false);
    }
    const addJob = document.getElementById("add-job-wrap");
    if (addJob?.classList.contains("open") && !addJob.contains(e.target)) {
      setAddJobPopoverOpen(false);
    }
    // UI-032: dismiss pinned Fill/Resume menus on outside click.
    for (const id of ["fill-wrap", "resume-wrap"]) {
      const wrap = document.getElementById(id);
      if (wrap?.classList.contains("open") && !wrap.contains(e.target)) {
        wrap.classList.remove("open");
      }
    }
  });
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    // Blur so :focus-within hover-pops dismiss; also close pinned Filters/Add/Fill/Resume.
    const brand = document.getElementById("brand-wrap");
    if (brand?.classList.contains("open") || brand?.contains(document.activeElement)) {
      e.preventDefault();
      setBrandPopoverOpen(false);
      (document.activeElement)?.blur?.();
      return;
    }
    const pinnedHover = document.querySelector(".hover-pop.open");
    if (pinnedHover) {
      e.preventDefault();
      pinnedHover.classList.remove("open");
      (document.activeElement)?.blur?.();
      return;
    }
    const hover = e.target?.closest?.(".hover-pop");
    if (hover) {
      e.preventDefault();
      (document.activeElement)?.blur?.();
      return;
    }
    if (addJobPopoverIsVisible()) {
      e.preventDefault();
      setAddJobPopoverOpen(false);
      return;
    }
    if (document.activeElement?.id === "search") return;
    if (!filtersPopoverIsVisible()) return;
    e.preventDefault();
    setFiltersPopoverOpen(false);
  });

  document.getElementById("tl-toggle")?.addEventListener("click", e => {
    e.stopPropagation();
    setTimelineCollapsed(!timelineCollapsed);
  });
  document.getElementById("timeline-head")?.addEventListener("click", () => {
    if (timelineCollapsed) setTimelineCollapsed(false);
  });
  // Re-arm the 10s timer while the user is still interacting with the timeline
  // (pointer, scroll, wheel) — pointerdown alone misses scroll-through reading.
  const tlPane = document.getElementById("timeline-pane");
  tlPane?.addEventListener("pointerdown", armTimelineAutoCollapseOnInteraction);
  tlPane?.addEventListener("scroll", armTimelineAutoCollapseOnInteraction, true);
  tlPane?.addEventListener("wheel", armTimelineAutoCollapseOnInteraction, { passive: true });
  // Collapse when focus / click moves outside the timeline.
  document.addEventListener("pointerdown", e => {
    if (timelineCollapsed) return;
    if (eventInsideTimeline(e.target)) return;
    setTimelineCollapsed(true);
  }, true);
  document.addEventListener("focusin", e => {
    if (timelineCollapsed) return;
    if (eventInsideTimeline(e.target)) return;
    setTimelineCollapsed(true);
  }, true);

  document.getElementById("add-job-url")?.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); addJobByUrl(); }
  });
}

/**
 * Shared fetch helper for the action handlers. Encapsulates the repeated
 *   fetch(...) → res.json().catch(() => ({})) → if (!res.ok) alert(...)
 * dance. Returns { res, data, ok, status } so callers keep their own
 * poll()/refresh/success side effects.
 *
 * Body handling (POST default): an object is JSON.stringify'd, a string is
 * sent verbatim, and `undefined` sends no body (and no Content-Type) so
 * bodyless DELETEs match their hand-written form byte-for-byte.
 *
 * On a non-ok response: `onError(data, res)` runs if provided; else, when
 * `alertOnError` (default true), it alerts `data.error || failMsg ||
 * "<failLabel> failed (<status>)"`. Pass `alertOnError: false` for handlers
 * that surface errors their own way (feedback text, custom messages).
 */
async function apiPost(url, body, opts = {}) {
  const {
    onError,
    failMsg,
    failLabel,
    alertOnError = true,
    method = "POST",
    headers,
  } = opts;
  const init = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json", ...(headers || {}) };
    init.body = typeof body === "string" ? body : JSON.stringify(body);
  } else if (headers) {
    init.headers = headers;
  }
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (typeof onError === "function") {
      onError(data, res);
    } else if (alertOnError) {
      alert(
        data.error
        || failMsg
        || (failLabel ? `${failLabel} failed (${res.status})` : `Request failed (${res.status})`)
      );
    }
  }
  return { res, data, ok: res.ok, status: res.status };
}

async function resolveApplyUrl(jobId) {
  const { ok, data } = await apiPost(
    `/api/jobs/${encodeURIComponent(jobId)}/resolve-apply`,
    { write: true },
    { failLabel: "Resolve apply" },
  );
  if (!ok) return;
  // Stamp resolve fields immediately so detail refreshes before poll lands.
  const local = (typeof jobs !== "undefined" && Array.isArray(jobs))
    ? jobs.find(j => j && j.id === jobId)
    : null;
  if (local && data) {
    if (data.apply_url) local.apply_url = data.apply_url;
    if (data.apply_resolve_status != null) local.apply_resolve_status = data.apply_resolve_status;
    if (data.apply_resolve_reason != null) local.apply_resolve_reason = data.apply_resolve_reason;
    if (data.apply_resolve_at != null) local.apply_resolve_at = data.apply_resolve_at;
    if (data.apply_resolve_message != null) {
      local.apply_resolve_message = data.apply_resolve_message;
    } else if (data.apply_resolve_status === "ok") {
      delete local.apply_resolve_message;
    }
    if (typeof render === "function") render();
  }
  const conf = data.confidence || "low";
  const url = data.url || "";
  if (conf === "medium") {
    alert(
      "Found a possible ATS link but confidence is medium — not overwriting apply URL.\n"
      + (url || "")
    );
  } else if (conf !== "high") {
    const reason = data.reason || "";
    let msg = "Could not resolve a company ATS apply URL.";
    if (reason === "easy_apply") {
      msg = "Easy Apply only (stays on LinkedIn) — not automating apply. Leave as Easy Apply.";
    } else if (reason === "not_logged_in" || reason === "authwall") {
      msg = data.message
        || "Open LinkedIn resolve browser first: ./open_linkedin_resolve.sh";
    } else if (reason === "blocked_captcha") {
      msg = data.message
        || "CAPTCHA / bot check on LinkedIn — stopped (never solve). Try again later or resolve manually.";
    } else if (reason === "no_ats_host") {
      msg = "Search did not find a known ATS apply URL.";
    } else if (reason === "not_needed") {
      msg = "This job already has a company/ATS apply URL.";
    } else if (reason === "no_external_apply") {
      msg = data.message || "No offsite Apply redirect found on LinkedIn.";
    } else if (reason === "unfetchable_ats") {
      msg = data.message || "Landed on Workday/iCIMS — left unresolved.";
    } else if (data.message) {
      msg = data.message;
    }
    alert(msg);
  }
  await poll();
}

async function submitAnswer(jobId) {
  const answer = document.getElementById("answer").value.trim();
  if (!answer) return;
  await apiPost(`/api/jobs/${encodeURIComponent(jobId)}/answer`, { answer }, { alertOnError: false });
  await poll();
}

async function decideCommand(jobId, approve) {
  await apiPost(`/api/jobs/${encodeURIComponent(jobId)}/approve_command`, { approve }, { alertOnError: false });
  await poll();
}

/** Ops confirm dialog — resolves true on Continue, false on Cancel / Esc / backdrop. */
function showOpsConfirm({ title, message, cancelLabel = "Cancel", continueLabel = "Continue" }) {
  return new Promise((resolve) => {
    document.getElementById("jh-confirm-modal")?.remove();
    const backdrop = document.createElement("div");
    backdrop.id = "jh-confirm-modal";
    backdrop.className = "jh-modal-backdrop";
    backdrop.setAttribute("role", "dialog");
    backdrop.setAttribute("aria-modal", "true");
    backdrop.setAttribute("aria-labelledby", "jh-modal-title");
    backdrop.innerHTML =
      `<div class="jh-modal">`
      + `<div class="jh-modal-title" id="jh-modal-title">${escapeHtml(title)}</div>`
      + `<div class="jh-modal-body">${escapeHtml(message)}</div>`
      + `<div class="jh-modal-actions">`
      + `<button type="button" class="act" data-action="cancel">${escapeHtml(cancelLabel)}</button>`
      + `<button type="button" class="act primary" data-action="continue">${escapeHtml(continueLabel)}</button>`
      + `</div></div>`;
    const finish = (ok) => {
      document.removeEventListener("keydown", onKey, true);
      backdrop.remove();
      resolve(ok);
    };
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        finish(false);
      }
    };
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) finish(false);
    });
    backdrop.querySelector('[data-action="cancel"]')?.addEventListener("click", () => finish(false));
    backdrop.querySelector('[data-action="continue"]')?.addEventListener("click", () => finish(true));
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(backdrop);
    backdrop.querySelector('[data-action="continue"]')?.focus();
  });
}

/** Test Mode ON: warn once before any Start/Fill API call. */
function confirmTestModeFill() {
  return showOpsConfirm({
    title: "Test Mode",
    message:
      "This run uses dummy profile and dummy resume data only — not real applicant PII. For testing only.",
    cancelLabel: "Cancel",
    continueLabel: "Continue",
  });
}

/** Optimistic in-progress UI before /start round-trip (Fill feels instant). */
function applyFillStartLocally(jobId, { skipPartyrock, testMode }) {
  const job = jobs.find(j => j.id === jobId);
  if (!job) return;
  const prefix = testMode ? "[DUMMY/TEST] " : "";
  job.status = skipPartyrock ? "navigating" : "tailoring";
  job.status_detail = skipPartyrock
    ? `${prefix}Starting fill…`
    : `${prefix}Tailoring resume via PartyRock…`;
  job.updated_at = new Date().toISOString();
  // Stay on the current tab (usually Open). Status change drops the job from
  // the Open list; the dossier still shows this selectedId so tailoring is visible.
  syncPollTimers();
  render();
}

/** Drop list ETag + JSON cache so the next poll must fetch a body (not 304). */
function invalidateJobsListCache() {
  lastJobsJSON = null;
  lastJobsEtag = null;
}

async function startJob(jobId, opts = {}) {
  const testMode = testModeEnabled;
  if (testMode) {
    const ok = await confirmTestModeFill();
    if (!ok) return;
  }
  const resumeOnly = !!opts.resumeOnly;
  const forcePartyrock = !!opts.forcePartyrock || resumeOnly;
  let skipPartyrock;
  if (forcePartyrock || resumeOnly) {
    skipPartyrock = false;
  } else if (typeof opts.skipPartyrock === "boolean") {
    skipPartyrock = opts.skipPartyrock;
  } else {
    skipPartyrock = testMode && !partyRockEnabled;
  }
  applyFillStartLocally(jobId, { skipPartyrock, testMode });
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/start`, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({
      test_mode: testMode,
      skip_partyrock: skipPartyrock,
      partyrock: !skipPartyrock,
      force_partyrock: forcePartyrock,
      resume_only: resumeOnly,
    }),
  });
  if (res.status === 409) {
    const d = await res.json().catch(() => ({}));
    alert(d.error || "Can't start — this job is already running.");
    invalidateJobsListCache(); // force poll to drop optimistic status (not 304)
  } else if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    alert(d.error || `Fill failed (${res.status})`);
    invalidateJobsListCache(); // force poll to drop optimistic status (not 304)
  } else {
    try {
      const d = await res.json();
      if (d.skip_partyrock) {
        console.log(
          `[Fill] test_mode=${d.test_mode} PartyRock bypassed → ${d.fill_after_tailor}`
        );
      } else if (d.partyrock_url) {
        console.log(
          `[PartyRock] Fill test_mode=${d.test_mode} mode=${d.partyrock_mode} `
          + `force=${d.force_partyrock} url=${d.partyrock_url}`
        );
      }
    } catch (_) { /* ignore */ }
  }
  await poll();
}

function toggleTestMode() {
  testModeEnabled = !testModeEnabled;
  saveTestModeSetting(testModeEnabled);
  // Treat-as-on-file is Test Mode only — clear when switching to real.
  if (!testModeEnabled) {
    treatResumeOnFile.clear();
    saveTreatResumeOnFile();
  }
  invalidateFillModeDefaults();
  syncTestModeToggleUI();
  if (copyKitPanelJobId) fetchCopyKit(copyKitPanelJobId);
  render();
}

function togglePartyRock() {
  if (!testModeEnabled) {
    syncTestModeToggleUI();
    return;
  }
  partyRockEnabled = !partyRockEnabled;
  savePartyRockSetting(partyRockEnabled);
  // Recompute Fill default (PartyRock off → fill-only).
  invalidateFillModeDefaults();
  syncTestModeToggleUI();
  render();
}

function syncTestModeToggleUI() {
  const el = document.getElementById("test-mode-toggle");
  if (el) {
    el.classList.toggle("test-on", testModeEnabled);
    el.classList.toggle("test-off", !testModeEnabled);
    el.setAttribute("aria-pressed", testModeEnabled ? "true" : "false");
    el.setAttribute("aria-label", testModeEnabled ? "Test mode on" : "Test mode off");
    el.dataset.tooltip = testModeEnabled
      ? "Test mode ON: dummy profile + dummy resume. Fill never auto-submits."
      : "Test mode OFF: real profile. Fill never auto-submits.";
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
      hint.textContent = "Enable Test Mode to control PartyRock for Fill.";
    } else if (partyRockEnabled) {
      hint.textContent =
        "Fill uses PartyRock Testing, then dummy fill. Turn off to skip tailor and go straight to dummy fill.";
    } else {
      hint.textContent =
        "Fill skips PartyRock and uses dummy resume + DUMMY_PROFILE only. Never submits. Tailor + fill still forces PartyRock for one run.";
    }
  }
}

function surfaceDeletedJob(jobId) {
  const job = jobs.find(j => j.id === jobId);
  if (!job) return;
  const inDeleted = job.status === "deleted" || LEGACY_SKIPPED_STATUSES.has(job.status);
  if (!inDeleted) return;
  selectedId = jobId;
  render();
}

function surfaceOpenJob(jobId) {
  selectedId = jobId;
  render();
}

function surfaceAppliedJob(jobId) {
  selectedId = jobId;
  render();
}

function applyMarkedAppliedLocally(jobId) {
  const job = jobs.find(j => j.id === jobId);
  if (!job) return;
  const now = new Date().toISOString();
  job.status = "applied";
  job.status_detail = "Marked as applied by user from dashboard.";
  job.applied_at = job.applied_at || now;
  job.updated_at = now;
  lastJobsJSON = JSON.stringify(jobs);
}

async function cancelJob(jobId) {
  await apiPost(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {}, { failLabel: "Cancel" });
  await poll();
}

async function skipJob(jobId, reason) {
  if (!reason) {
    if (!confirm("Skip this job? It moves to Deleted (soft). Restore from Deleted anytime.")) {
      return;
    }
  }
  const { ok, data: d } = await apiPost(
    `/api/jobs/${encodeURIComponent(jobId)}/skip`,
    reason ? { reason } : {},
    { failLabel: "Skip" },
  );
  if (!ok) {
    await poll();
    return;
  }
  await poll();
  // Duplicate merge may keep this job as survivor — stay on the current tab.
  if (d.merged_into && d.deleted_id && d.deleted_id !== jobId) {
    surfaceOpenJob(jobId);
  } else {
    surfaceDeletedJob(d.deleted_id || jobId);
  }
}

async function restoreJob(jobId) {
  const { ok } = await apiPost(`/api/jobs/${encodeURIComponent(jobId)}/restore`, {}, { failLabel: "Restore" });
  if (!ok) {
    await poll();
    return;
  }
  await poll();
  selectedId = jobId;
  render();
}

async function markSubmitted(jobId) {
  const job = jobs.find(j => j.id === jobId);
  const status = job?.status || "";
  // UI-037: stronger confirm when marking applied before Ready or while running.
  let msg;
  if (status === "ready_for_review") {
    msg = "Mark this job as applied? (You submit on the employer site — we never auto-submit.)";
  } else if (ACTIVE_PROGRESS_STATUSES.has(status) || STUCK_STATUSES.has(status)) {
    msg = `This job is ${statusLabel(status) || status}.\n\n`
      + "Mark as applied? This cancels any running fill/tailor for this job. "
      + "Only do this if you already submitted on the employer site. We never auto-submit.";
  } else {
    msg = `This job is not Ready yet (status: ${statusLabel(status) || status || "unknown"}).\n\n`
      + "Mark as applied anyway? Only do this if you already submitted on the employer site. "
      + "We never auto-submit.";
  }
  if (!confirm(msg)) return;
  const { ok } = await apiPost(
    `/api/jobs/${encodeURIComponent(jobId)}/submitted`,
    {},
    { failLabel: "Mark applied" },
  );
  if (!ok) {
    await poll();
    return;
  }
  applyMarkedAppliedLocally(jobId);
  surfaceAppliedJob(jobId);
  await poll();
}

async function uploadJobResume(jobId, inputEl) {
  const file = inputEl && inputEl.files && inputEl.files[0];
  if (!file) return;
  const job = jobs.find(j => j.id === jobId);
  if (job && ACTIVE_PROGRESS_STATUSES.has(job.status)) {
    alert("Resume upload blocked while fill/tailor is running. Cancel first.");
    try { inputEl.value = ""; } catch (_) { /* ignore */ }
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
    if (!res.ok) {
      alert(d.error || `Upload failed (${res.status})`);
    } else {
      treatResumeOnFile.delete(jobId);
      saveTreatResumeOnFile();
      setSelectedFillMode(jobId, "with-resume");
      console.log(`[resume] uploaded ${d.resume_path || file.name}`);
    }
  } catch (e) {
    alert(`Upload failed: ${e}`);
  } finally {
    try { inputEl.value = ""; } catch (_) { /* ignore */ }
  }
  await poll();
}

async function deleteJob(jobId) {
  const job = jobs.find(j => j.id === jobId);
  // UI-038: stronger confirm when soft-deleting an applied record.
  const msg = job && job.status === "applied"
    ? "Delete this applied job from tracking?\n\nIt moves to Deleted (soft). URL tombstones still block rediscovery."
    : "Move to Deleted?";
  if (!confirm(msg)) return;
  const { ok } = await apiPost(`/api/jobs/${encodeURIComponent(jobId)}`, undefined, { method: "DELETE", failLabel: "Delete" });
  if (!ok) return;
  const j = jobs.find(x => x.id === jobId);
  if (j) {
    j.status = "deleted";
    j.deleted_reason = "user";
    j.deleted_at = new Date().toISOString();
    j.updated_at = j.deleted_at;
  }
  if (selectedId === jobId && queue !== "deleted") selectedId = null;
  lastJobsJSON = JSON.stringify(jobs);
  render();
  await poll();
}

async function emptyDeleted() {
  const n = jobs.filter(j => j.status === "deleted").length;
  if (!n) { alert("Deleted is empty."); return; }
  if (!confirm(`Permanently remove ${n} deleted job(s)? URL tombstones stay so discovery will not re-add them.`)) return;
  const emptyBtn = document.getElementById("empty-deleted-btn");
  if (emptyBtn) emptyBtn.disabled = true;
  try {
    const { ok } = await apiPost("/api/jobs/empty-deleted", {}, { failLabel: "Empty Deleted" });
    if (!ok) return;
    jobs = jobs.filter(j => j.status !== "deleted");
    if (selectedId && !jobs.some(j => j.id === selectedId)) selectedId = null;
    invalidateJobsListCache(); // force poll to accept server list (not 304)
    render();
    await poll();
  } finally {
    if (emptyBtn) emptyBtn.disabled = false;
  }
}

function selectedPruneReasons() {
  return Array.from(document.querySelectorAll("[data-prune-reason]:checked"))
    .map(el => el.dataset.pruneReason);
}

async function loadPruneSettings() {
  try {
    const res = await fetch("/api/prune/settings");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    const enabled = new Set(data.reasons || []);
    document.querySelectorAll("[data-prune-reason]").forEach(el => {
      el.checked = enabled.has(el.dataset.pruneReason);
    });
    const interval = document.getElementById("prune-interval");
    if (interval) interval.value = String(data.interval_s ?? 300);
  } catch (e) {
    const feedback = document.getElementById("prune-feedback");
    if (feedback) {
      feedback.style.color = "var(--red)";
      feedback.textContent = `Settings unavailable: ${e}`;
    }
  }
}

async function savePruneSettings() {
  const interval = Number(document.getElementById("prune-interval")?.value ?? 300);
  const feedback = document.getElementById("prune-feedback");
  try {
    const res = await fetch("/api/prune/settings", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ reasons: selectedPruneReasons(), interval_s: interval }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    if (feedback) {
      feedback.style.color = "var(--text-dim)";
      feedback.textContent = "Schedule saved";
    }
  } catch (e) {
    if (feedback) {
      feedback.style.color = "var(--red)";
      feedback.textContent = `Save failed: ${e}`;
    }
  }
}

async function runPrune() {
  const button = document.getElementById("prune-now-btn");
  const feedback = document.getElementById("prune-feedback");
  if (button) button.disabled = true;
  if (feedback) {
    feedback.style.color = "var(--text-dim)";
    feedback.textContent = "Pruning…";
  }
  try {
    const reasons = selectedPruneReasons();
    const res = await fetch("/api/prune", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ reasons }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    if (feedback) {
      feedback.style.color = "var(--green)";
      feedback.textContent = `${data.moved || 0} pruned`;
    }
    await poll();
  } catch (e) {
    if (feedback) {
      feedback.style.color = "var(--red)";
      feedback.textContent = `Prune failed: ${e}`;
    }
  } finally {
    if (button) button.disabled = false;
  }
}

async function abortDiscoverSource(sourceId) {
  if (!sourceId) return;
  const { data: d } = await apiPost("/api/discover/abort", { source_id: sourceId }, { failLabel: "Abort source" });
  if (d.discovery) discoveryState = d.discovery;
  syncDiscoverUI();
  await pollStatus();
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
  if (!btn.querySelector(".radar-sweeping")) btn.innerHTML = DISCOVER_RADAR_SVG;
  const startLabel = fresh
    ? "Starting fresh…"
    : ((discoveryState && discoveryState.resume_available) ? "Continuing previous run…" : "Discovering…");
  btn.dataset.tooltip = startLabel;
  btn.setAttribute("aria-label", startLabel);
  const res = await fetch("/api/discover", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({
      sources,
      fresh: !!fresh,
      source_days: (discoveryState && discoveryState.source_days) || {},
      discover_worldwide: getEnabledRegions().includes("worldwide") || getEnabledRegions().includes("us"),
      discover_india: getEnabledRegions().includes("india"),
    }),
  });
  const d = await res.json().catch(() => ({}));
  if (res.status === 409) {
    alert(d.error || "Can't run discovery - it's already running.");
  } else if (res.status === 400) {
    alert(d.error || "Enable at least one discovery source.");
  }
  if (d.discovery) discoveryState = d.discovery;
  updateEnabledRegionsFromDiscovery(discoveryState);
  syncDiscoverUI();
  await pollStatus();
  await poll();
}

function toggleDiscoveryRun() {
  // UI-031: explicit start vs abort (same handler; distinct glyph/labels in syncDiscoverUI).
  if (discoveryState && discoveryState.running) {
    abortDiscover();
  } else {
    runDiscover();
  }
}

async function abortDiscover() {
  const { data: d } = await apiPost("/api/discover/abort", { all: true }, { alertOnError: false });
  if (d.discovery) discoveryState = d.discovery;
  syncDiscoverUI();
  await pollStatus();
}

let discoveryState = null;
let cronState = null;
let lastRuntimeJSON = null;

function sourceStatusLabel(s) {
  return ({ pending: "pending", collecting: "collecting", completed: "done",
    stopped: "stopped", failed: "failed", skipped: "off" })[s] || s || "pending";
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
  if (disc.resume_available && outcome !== "interrupted") {
    parts.push("will continue");
  }
  return parts.join(" · ");
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
  const phase = running
    ? (disc.phase_label || (disc.resumed ? "Continuing previous run…" : "Discovering…"))
    : (disc && disc.resume_available
      ? (disc.error || "Incomplete — click Discover to continue")
      : (hasRun
        ? (disc.ok === true ? "Completed" : (disc.error || "Finished"))
        : "Toggle sources, then Discover"));
  const lastRun = lastDiscoverRunLabel(disc);
  const total = runSources.reduce((n, s) => n + (s.count || 0), 0);
  const cronOn = !!(cronState && cronState.enabled);
  const cronHm = (cronState && cronState.hm) || "09:00";
  const cronHint = (cronState && cronState.hint) || "Enables job-hunter-daily OpenClaw cron.";
  const cronBlock = `<div class="discover-cron-block">
    <div class="pop-title" style="margin-bottom:6px">Scheduled discover</div>
    <label class="cron-toggle" style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
      <input type="checkbox" id="cron-toggle" ${cronOn ? "checked" : ""} onchange="toggleCron()">
      <span id="cron-label" class="${cronOn ? "cron-on" : "cron-off"}">${cronOn ? "Cron ON" : "Cron OFF"}</span>
    </label>
    <label class="cron-time-row" style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <span>Run at</span>
      <input type="time" id="cron-time" value="${escapeHtml(cronHm)}" step="60">
      <button type="button" class="primary" onclick="saveCronSchedule()">Save</button>
    </label>
    <div class="pop-hint" id="cron-hint">${escapeHtml(cronHint)}</div>
  </div>
  <div class="pop-sep" style="border-top:1px solid var(--border);margin:10px 0"></div>`;
  const regs = getEnabledRegions();
  const wwOn = regs.includes("worldwide") || regs.includes("us");
  const indiaOn = regs.includes("india");
  const regionBlock = `<div class="discover-region-block">
    <div class="pop-title" style="margin-bottom:6px">Lanes</div>
    <label class="region-toggle" style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <input type="checkbox" id="region-india-toggle" ${indiaOn ? "checked" : ""}
        onchange="toggleDiscoverRegion('india', this.checked)" ${running ? "disabled" : ""}>
      <span>India</span>
    </label>
    <label class="region-toggle" style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
      <input type="checkbox" id="region-ww-toggle" ${wwOn ? "checked" : ""}
        onchange="toggleDiscoverRegion('worldwide', this.checked)" ${running ? "disabled" : ""}>
      <span>Worldwide</span>
    </label>
    <div class="pop-hint">India = India roles (₹). Worldwide = non-India + US remote (native currencies). US onsite/hybrid are dropped.</div>
  </div>
  <div class="pop-sep" style="border-top:1px solid var(--border);margin:10px 0"></div>`;
  const rows = catalog.map(c => {
    const indiaOnly = !!c.india_only || isIndiaOnlySource(c.id);
    const wwOnly = !!c.worldwide_only || c.lane === "worldwide";
    const scrapeStatus = c.scrape_status || "";
    const forcedOff = (indiaOnly && !indiaOn) || (wwOnly && !wwOn && !indiaOnly);
    const on = !forcedOff && enabledMap[c.id] !== false;
    const live = byId[c.id];
    const st = forcedOff ? "skipped" : (live ? (live.status || "pending") : (on ? "idle" : "skipped"));
    let count = "—";
    if (st === "collecting") count = (live && live.count != null) ? live.count : 0;
    else if (live && live.count != null) count = live.count;
    const detail = forcedOff
      ? (indiaOnly ? "India lane off" : "Worldwide lane off")
      : (live ? (live.detail || "") : (on ? (scrapeStatus && scrapeStatus !== "active" && scrapeStatus !== "rss" && scrapeStatus !== "api" ? scrapeStatus : "") : "Disabled"));
    const canAbortSrc = running && live && live.status === "collecting";
    const recency = sourceSupportsRecency(c);
    const daysVal = effectiveSourceDays(c.id, disc);
    const daysOpts = Array.from({ length: SOURCE_DAYS_MAX - SOURCE_DAYS_MIN + 1 }, (_, i) => {
      const d = SOURCE_DAYS_MIN + i;
      return `<option value="${d}" ${d === daysVal ? "selected" : ""}>${d}d</option>`;
    }).join("");
    const daysControl = recency
      ? `<select class="src-days" data-source-id="${escapeHtml(c.id)}"
          title="Look back this many days"
          onchange="saveSourceDaysSetting(this.dataset.sourceId, this.value)"
          ${running || forcedOff ? "disabled" : ""}>${daysOpts}</select>`
      : `<span class="src-days src-days-na" title="This source has no date filter">${escapeHtml(scrapeStatus || "full board")}</span>`;
    const srcDomId = `disc-src-${c.id}`;
    const laneTag = indiaOnly ? " <span class=\"src-tag\">IN</span>"
      : (wwOnly ? " <span class=\"src-tag\">WW</span>" : "");
    return `<div class="discover-src-opt ${on ? "src-on" : "src-off"} ${forcedOff ? "src-forced-off" : ""} ${escapeHtml(st)}">
      <input type="checkbox" id="${escapeHtml(srcDomId)}" class="src-check" data-source-id="${escapeHtml(c.id)}" ${on ? "checked" : ""}
        ${forcedOff ? "disabled" : ""}
        onchange="toggleDiscoverySource(this.dataset.sourceId, this.checked)">
      <label class="src-main" for="${escapeHtml(srcDomId)}">
        <span class="src-label ${on ? "src-on" : "src-off"}">${escapeHtml(c.label || c.id)}${laneTag}</span>
        ${detail ? `<span class="src-detail">${escapeHtml(detail)}</span>` : ""}
      </label>
      ${daysControl}
      <span class="src-count">${count}</span>
      <span class="src-status">${escapeHtml(sourceStatusLabel(st === "idle" ? "pending" : st))}</span>
      ${canAbortSrc ? `<button type="button" class="src-abort" onclick="abortDiscoverSource('${jsStringEscape(c.id)}')" title="Stop ${escapeAttr(c.label || c.id)}">Abort</button>` : ""}
    </div>`;
  }).join("");
  el.innerHTML = `
    ${cronBlock}
    ${regionBlock}
    <div class="pop-head">
      <div class="pop-title">Discovery sources</div>
      <div class="pop-phase">${escapeHtml(phase)}</div>
    </div>
    ${lastRun ? `<div class="pop-hint discover-last-run" style="margin:0 0 8px">${escapeHtml(lastRun)}</div>` : ""}
    ${rows}
    <div class="pop-actions">
      <span style="flex:1;color:var(--text-dim);font-size:11px">${
        hasRun || running ? `${total} collected` : "Choices saved locally"
      }</span>
      ${!running
        ? `<button type="button" onclick="runDiscover(true)" title="Clear checkpoint and start a new pass (still skips known URLs)">Fresh run</button>`
        : ""}
      ${running && disc.can_abort !== false
        ? `<button type="button" onclick="abortDiscover()" style="color:var(--red);border-color:#e0555555">Abort all</button>`
        : ""}
    </div>`;
}

function syncDiscoverUI() {
  const btn = document.getElementById("discover-btn");
  const wrap = document.getElementById("discover-wrap");
  const disc = discoveryState;
  const running = !!(disc && disc.running);
  if (btn) {
    const wasRunning = btn.classList.contains("running");
    btn.disabled = false;
    if (running) {
      btn.classList.add("running");
      if (!wasRunning) btn.innerHTML = DISCOVER_RADAR_SVG;
      btn.dataset.tooltip = "Abort discovery (stop)";
      btn.setAttribute("aria-label", "Abort discovery");
    } else {
      btn.classList.remove("running");
      if (wasRunning || !btn.querySelector(".radar-idle")) btn.innerHTML = DISCOVER_RADAR_IDLE_SVG;
      btn.setAttribute("aria-label", "Start discovery");
      btn.dataset.tooltip = "Start discovery";
    }
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
  bar.classList.remove("activity-ready", "activity-active");
  if (disc && disc.running) {
    bar.classList.add("visible", "discovery");
    text.textContent = disc.phase_label
      || (disc.resumed ? "Continuing previous run…" : "Discovering…");
    if (meta) {
      if (disc.phase === "resolving" && disc.resolve_total != null) {
        meta.textContent = `${disc.resolve_done || 0}/${disc.resolve_total} apply links`;
      } else {
        const collecting = (disc.sources || []).filter(s => s.status === "collecting").length;
        const total = (disc.sources || []).reduce((n, s) => n + (s.count || 0), 0);
        meta.textContent = `${total} listings · ${collecting} source${collecting === 1 ? "" : "s"} active`;
      }
    }
    return;
  }
  if (jobs.length) {
    bar.classList.add("visible");
    bar.classList.remove("discovery");
    const j = jobs[0];
    const activity = jobActivityDot(j);
    if (activity) bar.classList.add(`activity-${activity}`);
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
    const regionsChanged = updateEnabledRegionsFromDiscovery(discoveryState);
    syncDiscoverUI();
    updateStatusBar(data);
    // Region toggles affect the Open list + mission stats — re-render on change.
    if (regionsChanged) render();
  } catch (e) { /* ignore */ }
}

async function loadActivity() {
  if (!selectedId) return;
  const jobId = selectedId;
  try {
    const res = await fetch(`/api/jobs/${jobId}/activity`);
    const data = await res.json();
    // Ignore stale responses if the user selected another job mid-flight.
    if (selectedId !== jobId) return;
    activityEvents = data.events || [];
    renderTimeline();
  } catch (e) { /* ignore */ }
}

let lastJobsJSON = null;

let _pollSeq = 0;
let lastJobsEtag = null;

const POLL_JOBS_MS_ACTIVE = 3000;
const POLL_JOBS_MS_IDLE = 10000;
const POLL_STATUS_MS_ACTIVE = 1500;
const POLL_STATUS_MS_IDLE = 5000;

let _jobsPollTimer = null;
let _statusPollTimer = null;
/** null = not yet scheduled; true/false = active vs idle cadence */
let _pollTimersActive = null;

function hasActivePipelineJobs(list) {
  for (const j of list || []) {
    const b = queueBucket(j && j.status);
    if (b === "progress" || b === "stuck" || b === "ready") return true;
  }
  return false;
}

/** Slow /api/jobs + /api/status when nothing is in progress/stuck/ready. */
function syncPollTimers(forceActive) {
  const active = forceActive != null ? !!forceActive : hasActivePipelineJobs(jobs);
  if (
    _pollTimersActive === active
    && _jobsPollTimer != null
    && _statusPollTimer != null
  ) {
    return;
  }
  _pollTimersActive = active;
  if (_jobsPollTimer != null) clearInterval(_jobsPollTimer);
  if (_statusPollTimer != null) clearInterval(_statusPollTimer);
  _jobsPollTimer = setInterval(
    poll,
    active ? POLL_JOBS_MS_ACTIVE : POLL_JOBS_MS_IDLE,
  );
  _statusPollTimer = setInterval(
    pollStatus,
    active ? POLL_STATUS_MS_ACTIVE : POLL_STATUS_MS_IDLE,
  );
}


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
  render();
}

function setAppliedSortFromSelect(value) {
  if (!value || value === appliedSortKey) return;
  appliedSortKey = value;
  appliedSortDir = value === "date" ? "desc" : "asc";
  render();
}

function toggleAppliedSortDir() {
  appliedSortDir = appliedSortDir === "asc" ? "desc" : "asc";
  render();
}

function setAppliedTableHidden(hidden) {
  appliedTableHidden = !!hidden;
  renderDossier();
}

function toggleAppliedTableHidden() {
  setAppliedTableHidden(!appliedTableHidden);
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
  render();
  requestAnimationFrame(() => document.querySelector("#applied-edit-form input")?.focus());
}

function cancelAppliedEditor() {
  editingAppliedId = null;
  render();
}

function openApplyUrlEditor(jobId) {
  editingApplyUrlId = jobId;
  selectedId = jobId;
  render();
  requestAnimationFrame(() => {
    const input = document.getElementById("apply-url-input");
    if (input) {
      input.focus();
      input.select();
    }
  });
}

function cancelApplyUrlEditor() {
  editingApplyUrlId = null;
  render();
}

async function saveApplyUrl(jobId) {
  const input = document.getElementById("apply-url-input");
  if (!input) return;
  const applyUrl = String(input.value || "").trim();
  if (!applyUrl) {
    alert("Apply URL is required");
    return;
  }
  try {
    new URL(applyUrl);
  } catch (_) {
    alert("Apply URL must be a valid http(s) URL");
    return;
  }
  if (!/^https?:\/\//i.test(applyUrl)) {
    alert("Apply URL must start with http:// or https://");
    return;
  }
  const saveBtn = document.querySelector(".apply-url-editor .linkish:not(.muted)");
  if (saveBtn) saveBtn.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/apply-url`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ apply_url: applyUrl }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save apply URL");
    const updated = result.job;
    if (updated && updated.id) {
      const idx = jobs.findIndex(j => j.id === updated.id);
      if (idx >= 0) jobs[idx] = { ...jobs[idx], ...updated };
      lastJobsJSON = JSON.stringify(jobs);
    }
    editingApplyUrlId = null;
    invalidateJobsListCache();
    render();
  } catch (error) {
    alert(error.message || "Could not save apply URL");
    if (saveBtn) saveBtn.disabled = false;
  }
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
      const resumeName = resumeDisplayName(job) || "Resume";
      const resumeCell = job.resume_path
        ? `<a href="/resume/${encodeURIComponent(job.id)}" target="_blank" rel="noopener" title="${escapeHtml(resumeName)}" onclick="event.stopPropagation()">${escapeHtml(resumeName)}</a>`
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

  const metaHint = appliedTableHidden ? "" : " · click a row for details";
  const toggleLabel = appliedTableHidden ? "▶ Show table" : "▼ Hide table";
  const toggleTitle = appliedTableHidden ? "Show tracking table" : "Hide tracking table";

  return `
    <div class="applied-tracking${appliedTableHidden ? " applied-tracking-hidden" : ""}">
      <div class="applied-tracking-header">
        <div>
          <h2>Applied applications</h2>
          <div class="applied-tracking-meta">${countText}${metaHint}</div>
        </div>
        <div class="applied-tracking-actions">
          ${appliedTableHidden ? "" : `<div class="applied-tracking-sort">
            <label>Sort
              <select onchange="setAppliedSortFromSelect(this.value)" aria-label="Sort applied applications">
                ${sortOptions}
              </select>
            </label>
            <button type="button" onclick="toggleAppliedSortDir()" title="Toggle sort direction">${dirLabel}</button>
          </div>`}
          <button type="button" class="applied-table-toggle icon-btn"
            onclick="toggleAppliedTableHidden()"
            title="${escapeAttr(toggleTitle)}"
            aria-expanded="${appliedTableHidden ? "false" : "true"}">${toggleLabel}</button>
        </div>
      </div>
      ${appliedTableHidden ? "" : body}
    </div>`;
}

function selectAppliedJob(id) {
  scrollToAppliedDetail = true;
  selectJob(id);
}

/** Job ids already announced (or seeded) as ready_for_review this page session. */
const spokenReadyForReviewIds = new Set();
let readyForReviewSpeechSeeded = false;

function sanitizeForSpeech(s) {
  return String(s || "")
    .replace(/[^\p{L}\p{N}\s&'.-]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

async function speakReadyForReview(job) {
  if (!job || !job.id || spokenReadyForReviewIds.has(job.id)) return;
  // Claim locally first so this tab can't double-fire while the claim is
  // in flight; the server then decides which single client actually speaks.
  spokenReadyForReviewIds.add(job.id);
  const company = sanitizeForSpeech(job.company);
  const title = sanitizeForSpeech(job.title);
  const parts = [company, title].filter(Boolean);
  if (!parts.length) return;
  if (typeof speechSynthesis === "undefined") return;
  // Every open dashboard tab polls independently, so without a shared claim
  // one Ready event is announced once per tab. The server grants it to one.
  try {
    const res = await fetch(
      `/api/jobs/${encodeURIComponent(job.id)}/claim-ready-announcement`,
      { method: "POST" },
    );
    if (!res.ok) return;
    const d = await res.json().catch(() => ({}));
    if (!d.speak) return;
  } catch (e) {
    return;
  }
  const phrase = `${parts.join(" ")} ready for review`;
  try {
    speechSynthesis.speak(new SpeechSynthesisUtterance(phrase));
  } catch (e) { /* ignore */ }
}

/** Announce only on new ready_for_review transitions (not every poll / not page-load seed). */
function checkReadyForReviewAnnouncements(jobList) {
  const list = jobList || [];
  if (!readyForReviewSpeechSeeded) {
    for (const j of list) {
      if (j && j.status === "ready_for_review" && j.id) {
        spokenReadyForReviewIds.add(j.id);
      }
    }
    readyForReviewSpeechSeeded = true;
    return;
  }
  for (const j of list) {
    if (j && j.status === "ready_for_review") speakReadyForReview(j);
  }
}

async function poll() {
  const seq = ++_pollSeq;
  try {
    const headers = {};
    if (lastJobsEtag) headers["If-None-Match"] = lastJobsEtag;
    let res = await fetch("/api/jobs", { headers });
    if (seq !== _pollSeq) return;
    if (res.status === 304) {
      // A forced refresh (failed Start, empty-deleted) clears lastJobsJSON so
      // we must not keep optimistic/partial local jobs behind a 304.
      if (lastJobsJSON == null) {
        lastJobsEtag = null;
        res = await fetch("/api/jobs");
        if (seq !== _pollSeq) return;
      } else {
        lastPollAt = Date.now();
        setSyncState("live");
        syncPollTimers();
        return;
      }
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const etag = res.headers.get("ETag");
    if (etag) lastJobsEtag = etag;
    const data = await res.json();
    if (seq !== _pollSeq) return;
    lastPollAt = Date.now();
    const hold = !!data.fill_hold_active;
    const newJobsJSON = JSON.stringify(data.jobs || []);
    if (newJobsJSON === lastJobsJSON && hold === fillHoldActive) {
      setSyncState("live");
      syncPollTimers();
      return;
    }
    fillHoldActive = hold;
    lastJobsJSON = newJobsJSON;
    jobs = mergeJobsPreservingListTags(data.jobs || []);
    restampAllFromJdCache();
    checkReadyForReviewAnnouncements(jobs);
    syncPollTimers();
    // Keep unsaved inline edits intact if another job changes during polling.
    if (editingAppliedId || editingApplyUrlId) {
      setSyncState("live");
      return;
    }
    render();
  } catch (e) {
    console.error("poll failed", e);
    setSyncState("error");
  }
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
  const { ok } = await apiPost("/api/profile", parsed, { alertOnError: false });
  if (ok) alert("Profile saved."); else alert("Save failed.");
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
  const hm = `${String(cronHour).padStart(2, "0")}:${String(cronMinute).padStart(2, "0")}`;
  let hint = "Turn on to schedule daily discovery.";
  if (enabled == null) hint = "Cron job not found (job-hunter-daily). Check OpenClaw cron.";
  else if (enabled) hint = `Runs job-hunter-daily discovery at ${formatCronClock(cronHour, cronMinute)} local.`;
  cronState = { enabled, hm, hint };
  const el = document.getElementById("cron-toggle");
  if (el) {
    el.disabled = enabled == null;
    el.checked = !!enabled;
  }
  const label = document.getElementById("cron-label");
  if (label) {
    label.textContent = enabled === true ? "Cron ON" : (enabled === false ? "Cron OFF" : "Cron");
    label.classList.toggle("cron-on", enabled === true);
    label.classList.toggle("cron-off", enabled === false);
    if (enabled == null) label.classList.remove("cron-on", "cron-off");
  }
  const timeEl = document.getElementById("cron-time");
  if (timeEl) {
    timeEl.disabled = enabled == null;
    timeEl.value = hm;
  }
  const hintEl = document.getElementById("cron-hint");
  if (hintEl) hintEl.textContent = hint;
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
    const { ok, data, status } = await apiPost("/api/cron/toggle", { enable }, { alertOnError: false });
    if (!ok) {
      alert(data.error || `Could not ${enable ? "enable" : "disable"} cron (${status})`);
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
  const { ok, data, status } = await apiPost("/api/cron/schedule", { hour, minute, time: raw }, { alertOnError: false });
  if (!ok) {
    alert(data.error || `Could not update schedule (${status})`);
    return;
  }
  cronHour = hour;
  cronMinute = minute;
  await loadCron();
}

try {
  bindOpsChrome();
} catch (chromeErr) {
  console.error("bindOpsChrome failed", chromeErr);
}
// job_sort.js should load first; fall back so a failed script tag doesn't brick render().
if (typeof compareByPosted !== "function") {
  console.error("job_sort.js missing — using posted-date sort fallbacks");
  globalThis.compareByPosted = (a, b) => {
    const at = Date.parse((a && (a.date_posted || a.date_posted_fallback)) || "") || 0;
    const bt = Date.parse((b && (b.date_posted || b.date_posted_fallback)) || "") || 0;
    return bt - at;
  };
}
if (typeof datePostedSortKey !== "function") {
  globalThis.datePostedSortKey = (job) => {
    const t = datePostedTime(job);
    return t == null ? -Infinity : t;
  };
}
if (typeof jobPostedDisplay !== "function") {
  globalThis.jobPostedDisplay = (job) => {
    const exact = job && job.date_posted;
    if (exact != null && exact !== "") {
      const t = Date.parse(exact);
      if (!Number.isNaN(t)) return { time: t, iso: exact, approx: false };
    }
    const fb = job && job.date_posted_fallback;
    if (fb != null && fb !== "") {
      const t = Date.parse(fb);
      if (!Number.isNaN(t)) return { time: t, iso: fb, approx: true };
    }
    return { time: null, iso: null, approx: false };
  };
}
if (typeof postedAgeLabel !== "function") {
  globalThis.postedAgeLabel = (job) => {
    const { time, approx } = jobPostedDisplay(job);
    if (time == null) return "—";
    const days = Math.max(0, Math.floor((Date.now() - time) / 86400000));
    return (approx ? "~" : "") + days + "d";
  };
}
try {
  setTimelineCollapsed(timelineCollapsed);
  render();
  markDashboardPainted();
  poll();
  pollStatus();
  loadCron();
  loadPruneSettings();
  renderDiscoverPopover(null);
  syncTestModeToggleUI();
  syncPollTimers();
  setInterval(loadCron, 15000);
  window.addEventListener("online", () => {
    poll();
    pollStatus();
  });
} catch (bootErr) {
  console.error("dashboard boot failed", bootErr);
  showBootError(`Dashboard failed to start (${bootErr}). Hard-refresh or restart the server.`);
  setSyncState("error");
}


// ---------------------------------------------------------- UI lifecycle
// Desktop app / browser tab close must stop the dashboard stack. Each tab
// gets a client_id; heartbeats track connected clients. Closing one of many
// tabs only removes that client — last tab sendBeacon / Quit / Cmd+Q shuts
// down. Idle heartbeat stall does NOT auto-quit.
// Refresh → POST /api/restart (cleanup + relaunch server) then reload *this*
// window in place — never window.close(). Quit / header X / last-window close
// → POST /api/shutdown (no restart flag); launcher then kills dedicated
// dashboard Chrome and exits the Dock applet.
const UI_CLIENT_STORAGE_KEY = "jobHunterDashboardClientId";
const UI_HEARTBEAT_MS = 5000;
let _dashboardRestartInFlight = false;
let _dashboardQuitInFlight = false;
// launch_dashboard.sh hard-reloads via Cmd+Shift+R; beforeunload must not kill the stack.
let _dashboardHardReloadInFlight = false;
window.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && String(e.key || "").toLowerCase() === "r") {
    _dashboardHardReloadInFlight = true;
  }
}, true);

/** Cursor Simple Browser / vscode webview previews — not a real dashboard quit target. */
function isEmbeddedDashboardView() {
  try {
    if (window.self !== window.top) return true;
    const p = String(window.location?.protocol || "");
    if (p === "vscode-webview:" || p === "cursor:") return true;
    const ua = String(navigator.userAgent || "");
    if (/Cursor|vscode|Simple Browser/i.test(ua)) return true;
  } catch (_) { /* cross-origin parent */ return true; }
  return false;
}

/** Hide the static HTML fallback only after the ops shell has painted (never on boot start). */
function markDashboardPainted() {
  if (document.body?.classList.contains("jh-booted")) return;
  const paint = () => {
    const header = document.querySelector(".ops-header");
    const headerVisible = header && header.offsetHeight > 0;
    if (headerVisible) {
      document.body?.classList.add("jh-header-visible");
    }
    document.body?.classList.add("jh-booted");
    document.getElementById("ops-shell")?.classList.add("jh-booted");
  };
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => requestAnimationFrame(paint));
  } else {
    paint();
  }
}

/**
 * UI-tied stack shutdown (pagehide → /api/shutdown) is for the Desktop Dock
 * applet only. Plain browser tabs must NOT kill :8787 on refresh — that caused
 * the permanent "can't reach the server / Retrying…" loop.
 * Opt in with ?desktop=1 (persists to localStorage jobHunterDesktopApp=1).
 */
function isDesktopDashboardApp() {
  try {
    const q = new URLSearchParams(window.location.search || "");
    if (q.get("desktop") === "1") {
      localStorage.setItem("jobHunterDesktopApp", "1");
      return true;
    }
    return localStorage.getItem("jobHunterDesktopApp") === "1";
  } catch (_) {
    return false;
  }
}

function shouldRunUiLifecycle() {
  if (isEmbeddedDashboardView()) return false;
  return isDesktopDashboardApp();
}

const REFRESH_BTN_ICON_HTML = `
  <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
    <path fill="currentColor" d="M13.65 2.35A7.96 7.96 0 0 0 8 0C3.58 0 0 3.58 0 8s3.58 8 8 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 8 14A6 6 0 1 1 8 2c1.66 0 3.14.69 4.22 1.78L9 7h7V0l-2.35 2.35z"/>
  </svg>
  <span>Refresh dashboard</span>`.trim();

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
  if (!shouldRunUiLifecycle()) return;
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
  // Embedded previews (Cursor Simple Browser) fire pagehide when discarded —
  // must not POST /api/shutdown and kill the stack for the real Desktop window.
  if (!shouldRunUiLifecycle()) return;
  // Refresh owns cleanup via /api/restart; Quit already POSTed /api/shutdown.
  // Hard reload (Desktop focus path) must not tear down the server mid-refresh.
  if (_dashboardRestartInFlight || _dashboardQuitInFlight || _dashboardHardReloadInFlight) return;
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
    const res = await fetch("/api/restart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: dashboardClientId() }),
      keepalive: true,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* dying mid-response */ }
    // Lifecycle-off (start_dashboard.sh): server stays up — soft reload only.
    if (data && data.soft_reload) {
      _dashboardRestartInFlight = false;
      if (btn) btn.disabled = false;
      if (quitBtn) quitBtn.disabled = false;
      window.location.reload();
      return;
    }
  } catch (e) { /* server dying mid-response is expected for hard restart */ }
  // Keep this window open. Launcher respawns server only; we reload in place.
  // ~60s covers shutdown cleanup + preferred-port reclaim (avoid :8788 hop).
  for (let i = 0; i < 120; i++) {
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
  alert("Dashboard did not come back within ~60s. Check logs/dashboard_server.out.");
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
  // Checkboxes / abort / cron controls in popover handle themselves; don't pin-toggle.
  if (e.target.closest("input, button, label.cron-toggle, label.cron-time-row, .discover-src-opt, .discover-cron-block")) return;
  wrap.classList.toggle("open");
});
document.addEventListener("click", (e) => {
  const wrap = document.getElementById("discover-wrap");
  if (wrap && !wrap.contains(e.target)) wrap.classList.remove("open");
});
// Logo hover menu: click/tap pins Refresh / Quit (hover/focus-within still works).
function setBrandPopoverOpen(open) {
  const wrap = document.getElementById("brand-wrap");
  if (!wrap) return;
  wrap.classList.toggle("open", !!open);
  wrap.setAttribute("aria-expanded", open ? "true" : "false");
}
document.getElementById("brand-wrap")?.addEventListener("click", (e) => {
  const wrap = document.getElementById("brand-wrap");
  if (!wrap) return;
  if (e.target.closest("button")) return;
  setBrandPopoverOpen(!wrap.classList.contains("open"));
});
document.addEventListener("click", (e) => {
  const wrap = document.getElementById("brand-wrap");
  if (wrap && !wrap.contains(e.target)) setBrandPopoverOpen(false);
});
// Checkbox focus keeps :focus-within popovers open after mouseleave — blur on leave
// unless the wrap is explicitly pinned (.open).
// list-filters is click / delayed-hover only (no :focus-within open).
["test-mode-wrap", "discover-wrap", "add-job-wrap", "prune-wrap", "brand-wrap"].forEach((id) => {
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
  // Settled jobs keep a stable lifecycle timeline from /activity on select —
  // do not re-poll (old OpenClaw session.tail fallback caused Applied flicker).
  if (!active) return;
  loadActivity();
  setTimeout(() => { if (selectedId === job.id) loadActivity(); }, 1200);
}, 2000);
