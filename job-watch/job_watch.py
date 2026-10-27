#!/usr/bin/env python3
"""
job_watch.py - daily job scanner for Vatsa's target companies.

Pulls open roles straight from each company's careers backend (Greenhouse,
Workday, Eightfold, Oracle HCM, Amazon, Atlassian, ...), keeps the ones that
match a Cloud / DevOps / SRE / Cloud-Security profile in the target locations,
scores them, and writes:

  reports/YYYY-MM-DD.html   - today's report (NEW roles highlighted)
  jobs.csv                  - every matching role ever seen (with first_seen date)
  state/seen.json           - IDs already reported (so "new" means new)

Standard library only (Python 3.9+). Run:  python3 job_watch.py
Options:  --all (show all matches, not just new)   --only "JPMorgan,Goldman"
          --no-notify   --open (open the report when done)
"""
import argparse, csv, datetime as dt, html, json, os, re, ssl, sys, time
import urllib.request, urllib.error, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state", "seen.json")
CSV_PATH = os.path.join(HERE, "jobs.csv")
REPORTS = os.path.join(HERE, "reports")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# ----------------------------------------------------------------------------
# PROFILE - edit freely
# ----------------------------------------------------------------------------
# A title must match at least one of these to be considered at all.
TITLE_INCLUDE = re.compile(
    r"site reliab|\bsre\b|devops|dev ops|devsecops|cloud|platform eng|platform/|"
    r"infrastructure|infra |\binfra\b|security eng|cloud security|identity|\biam\b|"
    r"access management|production eng|systems eng|reliability|finops|"
    r"solutions? architect|solution engineer|kubernetes|terraform|automation eng",
    re.I)
# ...and must not match any of these.
TITLE_EXCLUDE = re.compile(
    r"intern\b|internship|graduate|campus|new grad|2027|director|head of|\bvp\b|"
    r"vice president|principal|distinguished|staff|manager|lead\b|data center tech|"
    r"technician|helpdesk|desktop|sales|account exec|recruit|fpga|asic|hardware|"
    r"physical design|mechanical|electrical|construction|salesforce (developer|admin)",
    re.I)
# Keywords from the resume - each hit in title/description adds to the score.
SKILLS = {
    "aws": 3, "terraform": 3, "iam": 2, "identity": 2, "devsecops": 3, "devops": 2,
    "site reliability": 2, "sre": 2, "cloud security": 3, "cspm": 3, "prisma": 2,
    "github actions": 3, "ci/cd": 2, "oidc": 2, "kubernetes": 1, "eks": 1,
    "python": 1, "go ": 1, "golang": 1, "azure": 1, "gcp": 1, "compliance": 1,
    "governance": 2, "secrets": 2, "vault": 1, "finops": 2, "cost": 1,
    "infrastructure as code": 2, "iac": 2, "observability": 1, "grafana": 1,
    "splunk": 1, "landing zone": 2, "policy": 1, "automation": 1,
}
# Location filter (matched against the location text). Remote India counts.
LOC_OK = re.compile(
    r"bengaluru|bangalore|india|hyderabad|gurgaon|gurugram|mumbai|pune|noida|"
    r"singapore|london|united kingdom|\buk\b|edinburgh|glasgow|manchester|"
    r"dublin|ireland|amsterdam|netherlands|toronto|vancouver|montreal|canada|"
    r"remote|\bind\b|\bgbr\b|\bsgp\b|\birl\b|\bnld\b", re.I)
LOC_PREFERRED = re.compile(r"bengaluru|bangalore|remote - india|india - remote", re.I)

# ----------------------------------------------------------------------------
# COMPANIES  (all 53 rows from the IITD sheet; duplicates share one source)
# ----------------------------------------------------------------------------
# kinds: greenhouse | workday | eightfold | oracle | amazon | atlassian |
#        natwest | gs | janestreet | links (generic careers-page link scrape) |
#        manual (no reliable feed - listed in the report with a link)
COMPANIES = [
    # ---- Foreign quants
    {"name": "HRT", "kind": "greenhouse", "board": "wehrtyou"},
    {"name": "Citadel", "kind": "links", "url": "https://www.citadel.com/careers/open-opportunities/",
     "link_re": r"/careers/details/[^\"'#?]+"},
    {"name": "Tower Research (incl. Tower India)", "kind": "greenhouse", "board": "towerresearchcapital"},
    {"name": "Jane Street", "kind": "janestreet"},
    {"name": "Jump Trading", "kind": "greenhouse", "board": "jumptrading"},
    {"name": "Optiver (incl. Optiver Market Risk)", "kind": "links",
     "url": "https://www.optiver.com/working-at-optiver/career-opportunities/",
     "link_re": r"/join-us/jobs/[^\"'#?]+"},
    {"name": "IMC", "kind": "greenhouse", "board": "imc"},
    {"name": "Virtu", "kind": "manual", "url": "https://www.virtu.com/careers/"},
    {"name": "Da Vinci", "kind": "greenhouse", "board": "davinciderivatives"},
    {"name": "DRW", "kind": "greenhouse", "board": "drweng"},
    {"name": "Dynamic Technology Lab", "kind": "manual", "url": "https://www.dtl.com.sg/careers"},
    # ---- Indian quants
    {"name": "Quadeye", "kind": "manual", "url": "https://www.quadeye.com/careers"},
    {"name": "Graviton", "kind": "greenhouse", "board": "gravitonresearchcapital"},
    {"name": "NK Securities", "kind": "greenhouse", "board": "nksecuritiesresearch"},
    {"name": "Plus Wealth", "kind": "manual", "url": "https://www.pluswealth.in/careers"},
    {"name": "Daksh Trading", "kind": "manual", "url": "https://www.google.com/search?q=Daksh+Trading+careers"},
    {"name": "Atlas Research", "kind": "manual", "url": "https://www.google.com/search?q=Atlas+Research+quant+careers"},
    {"name": "Ebullient", "kind": "manual", "url": "https://www.ebullientsecurities.com/careers"},
    {"name": "Zanskar", "kind": "manual", "url": "https://www.zanskarsec.com/careers"},
    {"name": "QiCap", "kind": "manual", "url": "https://www.google.com/search?q=QiCap+careers"},
    {"name": "Trexquant", "kind": "manual", "url": "https://www.trexquant.com/careers"},
    {"name": "Squarepoint", "kind": "greenhouse", "board": "squarepointcapital"},
    {"name": "QRT", "kind": "greenhouse", "board": "quberesearchandtechnologies"},
    {"name": "MathSys", "kind": "manual", "url": "https://www.mathsys.co.uk/careers"},
    {"name": "Millennium", "kind": "eightfold", "host": "https://career.mlp.com", "domain": "mlp.com"},
    # ---- Tech
    {"name": "PocketFM", "kind": "manual", "url": "https://pocketfm.com/careers"},
    {"name": "Samsung Korea", "kind": "manual", "url": "https://www.samsung.com/global/careers/"},
    {"name": "Navi", "kind": "manual", "url": "https://navi.com/careers"},
    {"name": "Plivo", "kind": "manual", "url": "https://www.plivo.com/careers/"},
    {"name": "Eightfold", "kind": "manual", "url": "https://eightfold.ai/careers/"},
    {"name": "Flipkart", "kind": "manual", "url": "https://www.flipkartcareers.com/"},
    {"name": "LinkedIn", "kind": "manual", "url": "https://www.linkedin.com/jobs/search/?f_C=1337&keywords=site%20reliability"},
    {"name": "Salesforce", "kind": "workday", "host": "https://salesforce.wd12.myworkdayjobs.com",
     "tenant": "salesforce", "site": "External_Career_Site"},
    {"name": "Samsung Advanced Dev (SRI-B)", "kind": "manual", "url": "https://research.samsung.com/careers"},
    {"name": "Sprinklr", "kind": "manual", "url": "https://www.sprinklr.com/careers/"},
    {"name": "Walmart", "kind": "workday", "host": "https://walmart.wd5.myworkdayjobs.com",
     "tenant": "walmart", "site": "WalmartExternal"},
    {"name": "Google", "kind": "manual",
     "url": "https://www.google.com/about/careers/applications/jobs/results/?q=%22site%20reliability%22%20OR%20%22cloud%20security%22&location=Bengaluru%20Karnataka%20India"},
    {"name": "Amazon / AWS", "kind": "amazon"},
    {"name": "Atlassian", "kind": "atlassian"},
    {"name": "Accenture", "kind": "manual", "url": "https://www.accenture.com/in-en/careers/jobsearch?jk=cloud%20security"},
    {"name": "Microsoft", "kind": "eightfold", "host": "https://apply.careers.microsoft.com",
     "domain": "microsoft.com", "pcsx": True},
    {"name": "Adobe", "kind": "workday", "host": "https://adobe.wd5.myworkdayjobs.com",
     "tenant": "adobe", "site": "external_experienced"},
    # ---- Banks
    {"name": "D. E. Shaw", "kind": "manual", "url": "https://www.deshawindia.com/careers/work-with-us"},
    {"name": "Goldman Sachs (Quant + Tech)", "kind": "gs"},
    {"name": "JPMorgan (Trading + Software)", "kind": "oracle",
     "host": "https://jpmc.fa.oraclecloud.com", "site": "CX_1001"},
    {"name": "Jefferies", "kind": "manual", "url": "https://jefferies.tal.net/candidate"},
    {"name": "American Express", "kind": "manual", "url": "https://www.americanexpress.com/en-us/careers/"},
    {"name": "NatWest", "kind": "natwest"},
    # ================= ADDED: high-paying companies (30+ LPA at 5+ YOE) =================
    # ---- Cloud / security / dev-tools product companies
    {"name": "Okta", "kind": "greenhouse", "board": "okta", "tier": "added"},
    {"name": "Rubrik", "kind": "greenhouse", "board": "rubrik", "tier": "added"},
    {"name": "Databricks", "kind": "greenhouse", "board": "databricks", "tier": "added"},
    {"name": "Stripe", "kind": "greenhouse", "board": "stripe", "tier": "added"},
    {"name": "Cloudflare", "kind": "greenhouse", "board": "cloudflare", "tier": "added"},
    {"name": "Datadog", "kind": "greenhouse", "board": "datadog", "tier": "added"},
    {"name": "MongoDB", "kind": "greenhouse", "board": "mongodb", "tier": "added"},
    {"name": "Elastic", "kind": "greenhouse", "board": "elastic", "tier": "added"},
    {"name": "Twilio", "kind": "greenhouse", "board": "twilio", "tier": "added"},
    {"name": "Zscaler", "kind": "greenhouse", "board": "zscaler", "tier": "added"},
    {"name": "Netskope", "kind": "greenhouse", "board": "netskope", "tier": "added"},
    {"name": "Harness", "kind": "greenhouse", "board": "harnessinc", "tier": "added"},
    {"name": "GitLab", "kind": "greenhouse", "board": "gitlab", "tier": "added"},
    {"name": "Grafana Labs", "kind": "greenhouse", "board": "grafanalabs", "tier": "added"},
    {"name": "Samsara", "kind": "greenhouse", "board": "samsara", "tier": "added"},
    {"name": "Coinbase", "kind": "greenhouse", "board": "coinbase", "tier": "added"},
    {"name": "Airbnb", "kind": "greenhouse", "board": "airbnb", "tier": "added"},
    {"name": "Druva", "kind": "greenhouse", "board": "druva", "tier": "added"},
    {"name": "HighRadius", "kind": "greenhouse", "board": "highradius", "tier": "added"},
    {"name": "HackerRank", "kind": "greenhouse", "board": "hackerrank", "tier": "added"},
    {"name": "Snowflake", "kind": "ashby", "board": "snowflake", "tier": "added"},
    {"name": "Confluent", "kind": "ashby", "board": "confluent", "tier": "added"},
    {"name": "Notion", "kind": "ashby", "board": "notion", "tier": "added"},
    {"name": "Atlan", "kind": "ashby", "board": "atlan", "tier": "added"},
    {"name": "Snyk", "kind": "ashby", "board": "snyk", "tier": "added"},
    {"name": "Sysdig", "kind": "lever", "board": "sysdig", "tier": "added"},
    {"name": "ServiceNow", "kind": "smartrecruiters", "board": "ServiceNow", "tier": "added"},
    {"name": "NVIDIA", "kind": "workday", "host": "https://nvidia.wd5.myworkdayjobs.com",
     "tenant": "nvidia", "site": "NVIDIAExternalCareerSite", "tier": "added"},
    {"name": "CrowdStrike", "kind": "workday", "host": "https://crowdstrike.wd5.myworkdayjobs.com",
     "tenant": "crowdstrike", "site": "crowdstrikecareers", "tier": "added"},
    {"name": "F5", "kind": "workday", "host": "https://ffive.wd5.myworkdayjobs.com",
     "tenant": "ffive", "site": "f5jobs", "tier": "added"},
    {"name": "Palo Alto Networks", "kind": "manual", "url": "https://jobs.paloaltonetworks.com/en/search-jobs/India", "tier": "added"},
    {"name": "Nutanix", "kind": "manual", "url": "https://careers.nutanix.com/en/jobs/?search=site+reliability&country=India", "tier": "added"},
    {"name": "Cisco", "kind": "manual", "url": "https://jobs.cisco.com/jobs/SearchJobs/devops?21178=%5B169482%5D&21178_format=6020", "tier": "added"},
    {"name": "Intuit", "kind": "manual", "url": "https://jobs.intuit.com/search-jobs/site%20reliability/India", "tier": "added"},
    {"name": "Uber", "kind": "manual", "url": "https://www.uber.com/global/en/careers/list/?query=site%20reliability&location=IND-Karnataka-Bangalore", "tier": "added"},
    {"name": "Apple", "kind": "manual", "url": "https://jobs.apple.com/en-in/search?search=devops&location=india-INDC", "tier": "added"},
    # ---- Fintech / payments
    {"name": "PayPal", "kind": "workday", "host": "https://paypal.wd1.myworkdayjobs.com",
     "tenant": "paypal", "site": "jobs", "tier": "added"},
    {"name": "Mastercard", "kind": "workday", "host": "https://mastercard.wd1.myworkdayjobs.com",
     "tenant": "mastercard", "site": "CorporateCareers", "tier": "added"},
    {"name": "Visa", "kind": "manual", "url": "https://corporate.visa.com/en/jobs/?q=site%20reliability&cities=Bangalore", "tier": "added"},
    {"name": "Adyen", "kind": "greenhouse", "board": "adyen", "tier": "added"},
    {"name": "Wise", "kind": "greenhouse", "board": "wise", "tier": "added"},
    {"name": "Monzo", "kind": "greenhouse", "board": "monzo", "tier": "added"},
    {"name": "Brex", "kind": "greenhouse", "board": "brex", "tier": "added"},
    {"name": "Plaid", "kind": "ashby", "board": "plaid", "tier": "added"},
    {"name": "Razorpay", "kind": "greenhouse", "board": "razorpaysoftwareprivatelimited", "tier": "added"},
    {"name": "Groww", "kind": "greenhouse", "board": "groww", "tier": "added"},
    {"name": "CRED", "kind": "lever", "board": "cred", "tier": "added"},
    {"name": "FamPay", "kind": "lever", "board": "fampay", "tier": "added"},
    {"name": "PhonePe", "kind": "manual", "url": "https://www.phonepe.com/careers/job-openings/", "tier": "added"},
    {"name": "Zerodha", "kind": "manual", "url": "https://zerodha.com/careers/", "tier": "added"},
    # ---- Indian consumer tech
    {"name": "Meesho", "kind": "lever", "board": "meesho", "tier": "added"},
    {"name": "Swiggy", "kind": "manual", "url": "https://careers.swiggy.com/#/careers", "tier": "added"},
    {"name": "Zomato / Eternal", "kind": "manual", "url": "https://www.eternal.com/careers/", "tier": "added"},
    {"name": "Dream11", "kind": "manual", "url": "https://www.dreamsports.group/careers/", "tier": "added"},
    {"name": "Grab", "kind": "smartrecruiters", "board": "Grab", "tier": "added"},
    {"name": "Booking.com", "kind": "manual", "url": "https://jobs.booking.com/booking/jobs?keywords=site%20reliability", "tier": "added"},
    # ---- Funds / bank GCCs
    {"name": "Point72", "kind": "greenhouse", "board": "point72", "tier": "added"},
    {"name": "Arcesium", "kind": "manual", "url": "https://www.arcesium.com/careers/open-positions", "tier": "added"},
    {"name": "BlackRock", "kind": "workday", "host": "https://blackrock.wd1.myworkdayjobs.com",
     "tenant": "blackrock", "site": "BlackRock_Professional", "tier": "added"},
    {"name": "Deutsche Bank", "kind": "workday", "host": "https://db.wd3.myworkdayjobs.com",
     "tenant": "db", "site": "DBWebsite", "tier": "added"},
    {"name": "Citi", "kind": "workday", "host": "https://citi.wd5.myworkdayjobs.com",
     "tenant": "citi", "site": "2", "tier": "added"},
    {"name": "Barclays", "kind": "workday", "host": "https://barclays.wd3.myworkdayjobs.com",
     "tenant": "barclays", "site": "External_Career_Site_Barclays", "tier": "added"},
    {"name": "Morgan Stanley", "kind": "manual", "url": "https://morganstanley.eightfold.ai/careers?query=site%20reliability&location=Bengaluru", "tier": "added"},
    {"name": "Wells Fargo", "kind": "manual", "url": "https://www.wellsfargojobs.com/en/jobs/?search=site+reliability&country=India", "tier": "added"},
]

SEARCH_TERMS = ["site reliability", "devops", "cloud engineer", "cloud security",
                "security engineer", "infrastructure engineer", "platform engineer",
                "identity access", "solutions architect"]
SEARCH_LOCS = ["India", "Bengaluru", "Singapore", "London", "Dublin", "Amsterdam", "Toronto"]

# ----------------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------------
_ctx = ssl.create_default_context()

def http(url, data=None, headers=None, timeout=25, method=None):
    h = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
         "Accept-Language": "en-US,en;q=0.9"}
    h.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    last = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 403, 401, 410):
                raise
            last = e
        except Exception as e:  # noqa
            last = e
        time.sleep(1.5)
    raise last

def jget(url, **kw):
    return json.loads(http(url, **kw))

def strip(s):
    s = html.unescape(s or "")          # Greenhouse content arrives HTML-escaped
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def job(company, title, location, url, posted="", desc="", jid=None):
    return {"company": company, "title": strip(title), "location": strip(location),
            "url": url, "posted": (posted or "")[:10] if re.match(r"\d{4}-\d\d-\d\d", posted or "") else (posted or "").replace("Posted ", ""), "desc": strip(desc)[:4000],
            "id": f"{company}::{jid or url}"}

# ----------------------------------------------------------------------------
# Fetchers - each returns a list of job dicts (unfiltered)
# ----------------------------------------------------------------------------
def f_greenhouse(c):
    out, err = [], None
    for host in ("https://boards-api.greenhouse.io", "https://boards-api.eu.greenhouse.io"):
        try:
            d = jget(f"{host}/v1/boards/{c['board']}/jobs?content=true")
            for j in d.get("jobs", []):
                out.append(job(c["name"], j["title"], (j.get("location") or {}).get("name", ""),
                               j.get("absolute_url"), j.get("updated_at", ""),
                               j.get("content", ""), j.get("id")))
            return out
        except Exception as e:
            err = e
    raise err

def f_workday(c):
    out, seen = [], set()
    api = f"{c['host']}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    for term in SEARCH_TERMS:
        d = jget(api, data={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term})
        for p in d.get("jobPostings", []):
            path = p.get("externalPath", "")
            if path in seen:
                continue
            seen.add(path)
            out.append(job(c["name"], p.get("title", ""), p.get("locationsText", ""),
                           f"{c['host']}/{c['site']}{path}", p.get("postedOn", ""), "", path))
    return out

def f_eightfold(c):
    out, seen = [], set()
    for term in SEARCH_TERMS:
        for loc in SEARCH_LOCS:
            q = urllib.parse.urlencode({"domain": c["domain"], "query": term, "location": loc})
            if c.get("pcsx"):
                d = jget(f"{c['host']}/api/pcsx/search?{q}&start=0")
                ps = (d.get("data") or {}).get("positions", [])
            else:
                d = jget(f"{c['host']}/api/apply/v2/jobs?{q}&num=50")
                ps = d.get("positions", [])
            for p in ps:
                pid = str(p.get("id"))
                if pid in seen:
                    continue
                seen.add(pid)
                locs = p.get("locations") or [p.get("location", "")]
                ts = p.get("postedTs") or p.get("t_create")
                posted = dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
                url = p.get("canonicalPositionUrl") or f"{c['host']}/careers/job/{pid}"
                out.append(job(c["name"], p.get("name", ""), " / ".join(map(str, locs)), url,
                               posted, p.get("job_description", ""), pid))
    return out

def f_oracle(c):
    out, seen = [], set()
    for term in SEARCH_TERMS:
        for loc in SEARCH_LOCS:
            finder = (f'findReqs;siteNumber={c["site"]},keyword="{term}",location="{loc}",'
                      f'limit=25,sortBy=POSTING_DATES_DESC')
            url = (f"{c['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?"
                   f"onlyData=true&expand=requisitionList&finder={urllib.parse.quote(finder, safe='=;,')}")
            d = jget(url)
            for it in ((d.get("items") or [{}])[0].get("requisitionList") or []):
                rid = str(it.get("Id"))
                if rid in seen:
                    continue
                seen.add(rid)
                out.append(job(c["name"], it.get("Title", ""), it.get("PrimaryLocation", ""),
                               f"{c['host']}/hcmUI/CandidateExperience/en/sites/{c['site']}/job/{rid}",
                               it.get("PostedDate", ""), it.get("ShortDescriptionStr", ""), rid))
    return out

def f_amazon(c):
    out, seen = [], set()
    for term in ["devops", "site reliability", "cloud security", "security engineer",
                 "solutions architect", "systems development engineer", "cloud infrastructure"]:
        for cc in ["IND", "SGP", "GBR", "IRL", "NLD", "CAN"]:
            q = urllib.parse.urlencode({"base_query": term, "normalized_country_code[]": cc,
                                        "result_limit": 50, "sort": "recent"})
            d = jget(f"https://www.amazon.jobs/en/search.json?{q}")
            for j in d.get("jobs", []):
                jid = j.get("id_icims") or j.get("id")
                if jid in seen:
                    continue
                seen.add(jid)
                out.append(job(c["name"], j.get("title", ""),
                               j.get("normalized_location") or j.get("location", ""),
                               "https://www.amazon.jobs" + j.get("job_path", ""),
                               j.get("posted_date", ""),
                               (j.get("basic_qualifications", "") + " " + j.get("description_short", "")), jid))
    return out

def f_atlassian(c):
    d = jget("https://www.atlassian.com/endpoint/careers/listings")
    arr = d if isinstance(d, list) else d.get("listings", [])
    out = []
    for x in arr:
        locs = x.get("locations") or []
        url = x.get("applyUrl") or f"https://www.atlassian.com/company/careers/details/{x.get('id')}"
        desc = " ".join(str(x.get(k, "")) for k in ("overview", "responsibilities", "qualifications"))
        out.append(job(c["name"], x.get("title", ""), " / ".join(locs), url,
                       (x.get("portalJobPost") or {}).get("updatedDate", ""), desc, x.get("id")))
    return out

def f_janestreet(c):
    arr = jget("https://www.janestreet.com/jobs/main.json")
    out = []
    for x in arr:
        jid = x.get("id")
        title = x.get("position") or x.get("title") or x.get("name") or ""
        out.append(job(c["name"], title, x.get("city") or x.get("location") or "",
                       f"https://www.janestreet.com/join-jane-street/position/{jid}/",
                       "", x.get("overview", ""), jid))
    return out

def f_natwest(c):
    out, seen = [], set()
    for term in ["aws", "cloud", "devops", "site reliability", "infrastructure", "security"]:
        for loc in ["bangalore", "gurugram", "chennai", "london", "edinburgh", "manchester"]:
            try:
                page = http(f"https://jobs.natwestgroup.com/search/jobs/in/{loc}?q={urllib.parse.quote(term)}")
            except Exception:
                continue
            for m in re.finditer(r'href="((?:https://jobs\.natwestgroup\.com)?/jobs/(\d+)-[^"#?]+)"', page):
                path, jid = m.group(1), m.group(2)
                if jid in seen:
                    continue
                path = path.replace("https://jobs.natwestgroup.com", "")
                title = _slug_title(path)
                title = re.sub(r"\bAvp\b", "AVP", re.sub(r"\bVp\b", "VP", title))
                seen.add(jid)
                out.append(job(c["name"], title, loc.title(),
                               "https://jobs.natwestgroup.com" + path, "", "", jid))
    return out

GS_QUERY = ("query GetRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: "
            "$searchQueryInput) { totalCount items { roleId jobTitle corporateTitle division "
            "locations { city country } externalSource { sourceId } } } }")

def f_gs(c):
    # Goldman's job titles are generic ("Core Engineering-L2-Bengaluru-Associate-Software
    # Engineering"), so we trust the site's own relevance ranking for each search term and
    # keep Analyst/Associate roles; the report links straight to each role.
    out, seen = [], set()
    for term in ["site reliability engineer", "cloud engineer AWS terraform", "devops",
                 "cloud security", "infrastructure engineering", "identity access management"]:
        body = {"operationName": "GetRoles", "query": GS_QUERY, "variables": {"searchQueryInput": {
            "page": {"pageSize": 15, "pageNumber": 0},
            "sort": {"sortStrategy": "RELEVANCE", "sortOrder": "DESC"},
            "filters": [], "experiences": ["PROFESSIONAL"], "searchTerm": term}}}
        d = jget("https://api-higher.gs.com/gateway/api/v1/graphql", data=body,
                 headers={"Origin": "https://higher.gs.com", "Referer": "https://higher.gs.com/"})
        for it in ((d.get("data") or {}).get("roleSearch") or {}).get("items", []):
            sid = (it.get("externalSource") or {}).get("sourceId") or it.get("roleId")
            if sid in seen or it.get("corporateTitle") not in ("Analyst", "Associate"):
                continue
            seen.add(sid)
            loc = " / ".join(f'{l.get("city") or ""}, {l.get("country") or ""}' for l in it.get("locations") or [])
            j = job(c["name"], it.get("jobTitle", ""), loc, f"https://higher.gs.com/roles/{sid}",
                    "", f"search match: {term}", sid)
            j["trusted"] = True
            out.append(j)
    return out

def _slug_title(url):
    slug = url.rstrip("/").split("/")[-1]
    slug = re.sub(r"^\d+-", "", slug)
    return slug.replace("-", " ").replace("_", " ").title()

def f_links(c):
    page = http(c["url"])
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(c["url"]))
    out, seen = [], set()
    for m in re.finditer(r'href="(' + c["link_re"] + r')"', page):
        href = m.group(1)
        url = href if href.startswith("http") else base + href
        if url in seen:
            continue
        seen.add(url)
        # anchor text if it is simple, else derive the title from the URL slug
        tail = page[m.end(): m.end() + 600]
        txt = strip(tail.split("</a>")[0].split(">", 1)[-1]) if "</a>" in tail else ""
        title = txt if 3 < len(txt) < 140 else _slug_title(url)
        # Optiver URLs carry the city: /join-us/jobs/<team>/<city>/<slug>/
        parts = url.rstrip("/").split("/")
        loc = parts[-2].replace("-", " ").title() if "/join-us/jobs/" in url and len(parts) > 2 else ""
        out.append(job(c["name"], title, loc, url, "", "", url))
    return out

def f_lever(c):
    out = []
    for x in jget(f"https://api.lever.co/v0/postings/{c['board']}?mode=json"):
        cat = x.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location", "")]
        posted = dt.datetime.utcfromtimestamp(x["createdAt"] / 1000).strftime("%Y-%m-%d") if x.get("createdAt") else ""
        out.append(job(c["name"], x.get("text", ""), " / ".join(l for l in locs if l), x.get("hostedUrl"),
                       posted, x.get("descriptionPlain", "") + " " + x.get("additionalPlain", ""), x.get("id")))
    return out

def f_ashby(c):
    d = jget(f"https://api.ashbyhq.com/posting-api/job-board/{c['board']}")
    out = []
    for x in d.get("jobs", []):
        locs = [x.get("location", "")] + [s.get("location", "") for s in x.get("secondaryLocations") or []]
        out.append(job(c["name"], x.get("title", ""), " / ".join(l for l in locs if l), x.get("jobUrl"),
                       x.get("publishedAt", ""), x.get("descriptionPlain", ""), x.get("id")))
    return out

def f_smartrecruiters(c):
    out = []
    for off in range(0, 2000, 100):
        d = jget(f"https://api.smartrecruiters.com/v1/companies/{c['board']}/postings?limit=100&offset={off}")
        for x in d.get("content", []):
            loc = x.get("location") or {}
            out.append(job(c["name"], x.get("name", ""), f'{loc.get("city","")}, {loc.get("country","")}',
                           f"https://jobs.smartrecruiters.com/{c['board']}/{x.get('id')}",
                           x.get("releasedDate", ""), "", x.get("id")))
        if len(d.get("content", [])) < 100:
            break
    return out

FETCHERS = {"greenhouse": f_greenhouse, "workday": f_workday, "eightfold": f_eightfold,
            "oracle": f_oracle, "amazon": f_amazon, "atlassian": f_atlassian,
            "janestreet": f_janestreet, "lever": f_lever, "ashby": f_ashby,
            "smartrecruiters": f_smartrecruiters, "natwest": f_natwest, "gs": f_gs, "links": f_links}

# ----------------------------------------------------------------------------
# Matching / scoring
# ----------------------------------------------------------------------------
def score(j):
    t, text = j["title"].lower(), (j["title"] + " " + j["desc"]).lower()
    if TITLE_EXCLUDE.search(t) and not j.get("trusted"):
        return None
    if not TITLE_INCLUDE.search(t) and not (j.get("trusted") and re.search(r"engineer", t)):
        return None
    if j.get("trusted") and re.search(r"vice president|managing director", t):
        return None
    if j["location"] and not LOC_OK.search(j["location"]):
        return None
    s = 5
    for kw, w in SKILLS.items():
        if kw in text:
            s += w
    if re.search(r"site reliab|devops|cloud security|cloud engineer|iam|identity", t):
        s += 4
    if LOC_PREFERRED.search(j["location"]):
        s += 3
    m = re.search(r"(\d+)\s*\+?\s*(?:-\s*\d+\s*)?years", text)
    if m:
        yrs = int(m.group(1))
        j["years"] = yrs
        if yrs >= 7:
            s -= 6
        elif yrs <= 5:
            s += 2
    return s

# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def write_report(matches, new_ids, health, manual, show_all):
    os.makedirs(REPORTS, exist_ok=True)
    today = dt.date.today().isoformat()
    rows = [m for m in matches if show_all or m["id"] in new_ids]
    rows.sort(key=lambda m: (m["id"] not in new_ids, -m["score"]))
    def tr(m):
        badge = '<span class="new">NEW</span> ' if m["id"] in new_ids else ""
        yrs = f'{m["years"]}+ yrs' if m.get("years") else ""
        return (f'<tr><td>{m["score"]}</td><td>{html.escape(m["company"])}</td>'
                f'<td>{badge}<a href="{html.escape(m["url"])}" target="_blank">{html.escape(m["title"])}</a></td>'
                f'<td>{html.escape(m["location"][:80])}</td><td>{yrs}</td><td>{html.escape(m["posted"])}</td></tr>')
    hrows = "".join(
        f'<tr class="{ "bad" if h["error"] else "" }"><td>{html.escape(h["company"])}</td><td>{h["kind"]}</td>'
        f'<td>{h["fetched"]}</td><td>{h["matched"]}</td><td>{html.escape(h["error"] or "ok")}</td></tr>'
        for h in health)
    mlinks = "".join(f'<li><a href="{html.escape(c["url"])}" target="_blank">{html.escape(c["name"])}</a></li>'
                     for c in manual)
    doc = f"""<!doctype html><meta charset="utf-8"><title>Job Watch {today}</title>
<style>
body{{font:14px/1.45 -apple-system,Segoe UI,sans-serif;margin:24px;color:#1d1d1f;background:#fff}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#666;margin-bottom:18px}}
table{{border-collapse:collapse;width:100%;margin-bottom:28px}} th,td{{padding:6px 8px;border-bottom:1px solid #eee;text-align:left;vertical-align:top}}
th{{background:#f6f6f8;font-weight:600}} a{{color:#0a58ca;text-decoration:none}} a:hover{{text-decoration:underline}}
.new{{background:#1a7f37;color:#fff;border-radius:4px;padding:1px 6px;font-size:11px;font-weight:700}}
tr.bad td{{color:#b42318}} details{{margin-bottom:20px}} ul{{columns:3}}
@media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}} th{{background:#222}} td,th{{border-color:#2a2a2a}} a{{color:#6ea8fe}}}}
</style>
<h1>Job Watch &mdash; {today}</h1>
<div class="sub">{len(new_ids)} new matching role(s) today &middot; {len(matches)} matching roles open in total &middot;
{sum(1 for h in health if not h['error'])}/{len(health)} feeds OK</div>
<table><tr><th>Score</th><th>Company</th><th>Role</th><th>Location</th><th>Exp</th><th>Posted</th></tr>
{''.join(tr(m) for m in rows) or '<tr><td colspan=6>No new matching roles today.</td></tr>'}</table>
<details><summary>Check manually ({len(manual)} companies without a usable job feed)</summary><ul>{mlinks}</ul></details>
<details><summary>Feed health</summary><table><tr><th>Company</th><th>Source</th><th>Jobs fetched</th><th>Matches</th><th>Status</th></tr>{hrows}</table></details>
"""
    path = os.path.join(REPORTS, f"{today}.html")
    with open(path, "w") as f:
        f.write(doc)
    with open(os.path.join(REPORTS, "latest.html"), "w") as f:
        f.write(doc)
    return path

def notify(n_new, path):
    if sys.platform != "darwin":
        return
    msg = f"{n_new} new matching role(s)" if n_new else "No new matching roles today"
    os.system(f"""osascript -e 'display notification "{msg}" with title "Job Watch" subtitle "Open reports/latest.html"' >/dev/null 2>&1""")

# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="list all matches, not just new ones")
    ap.add_argument("--only", help="comma-separated company name fragments to run")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()

    comps = COMPANIES
    if a.only:
        frags = [x.strip().lower() for x in a.only.split(",")]
        comps = [c for c in comps if any(f in c["name"].lower() for f in frags)]
    auto = [c for c in comps if c["kind"] in FETCHERS]
    manual = [c for c in comps if c["kind"] == "manual"]

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    seen = {}
    if os.path.exists(STATE):
        with open(STATE) as f:
            seen = json.load(f)
    first_run = not seen

    matches, health = [], []
    def run(c):
        try:
            jobs = FETCHERS[c["kind"]](c)
            return c, jobs, None
        except Exception as e:
            return c, [], f"{type(e).__name__}: {str(e)[:120]}"
    with ThreadPoolExecutor(max_workers=8) as ex:
        for fut in as_completed([ex.submit(run, c) for c in auto]):
            c, jobs, err = fut.result()
            got = 0
            dedup = {}
            for j in jobs:
                s = score(j)
                if s is not None:
                    j["score"] = s
                    dedup[j["id"]] = j
            matches.extend(dedup.values())
            got = len(dedup)
            health.append({"company": c["name"], "kind": c["kind"], "fetched": len(jobs),
                           "matched": got, "error": err})
            print(f"  {c['name']:<40} {('ERR ' + err) if err else f'{len(jobs):>4} jobs, {got:>3} match'}")
    health.sort(key=lambda h: (h["error"] is None, h["company"]))

    today = dt.date.today().isoformat()
    new_ids = {m["id"] for m in matches if m["id"] not in seen}
    for m in matches:
        seen.setdefault(m["id"], today)
        m["first_seen"] = seen[m["id"]]
    with open(STATE, "w") as f:
        json.dump(seen, f)

    # append new rows to the CSV log
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["first_seen", "score", "company", "title", "location", "years", "posted", "url"])
        for m in sorted((m for m in matches if m["id"] in new_ids), key=lambda m: -m["score"]):
            w.writerow([today, m["score"], m["company"], m["title"], m["location"],
                        m.get("years", ""), m["posted"], m["url"]])

    path = write_report(matches, new_ids, health, manual, a.all or first_run)
    print(f"\n{len(new_ids)} new / {len(matches)} matching roles. Report: {path}")
    if not a.no_notify:
        notify(len(new_ids), path)
    if a.open and sys.platform == "darwin":
        os.system(f'open "{path}"')

if __name__ == "__main__":
    main()
