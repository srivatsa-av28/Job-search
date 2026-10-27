# Job Watch

Checks the careers sites of 112 companies every morning: the 53 from the IITD sheet plus 59 more that pay 30+ LPA at 5+ YOE. It keeps Cloud / DevOps / SRE / Cloud-Security roles in Bengaluru, India (incl. remote), Singapore, the UK, Ireland, the Netherlands and Canada, then writes a report that highlights roles that are new since the last run.

## Install (once)

Open Terminal and run:

    cd ~/Documents/job-watch
    bash install.sh

This copies the script to `~/job-watch`, schedules it for 09:00 every day (it runs when the Mac wakes if it was asleep), runs it once and opens the first report.

## Where results go (`~/job-watch`)

- `reports/latest.html`: today's report. NEW roles are at the top, ranked by fit score. Two collapsible sections list the companies to check by hand and show whether each feed worked.
- `jobs.csv`: running log of every new matching role, with the date it was first seen.
- `logs/run.log`: output from the scheduled runs.

## Useful commands

    python3 ~/job-watch/job_watch.py --all --open        # show every current match, not just new ones
    python3 ~/job-watch/job_watch.py --only "JPMorgan,Goldman"
    bash ~/Documents/job-watch/install.sh uninstall      # stop the daily run

## Tuning

At the top of `job_watch.py` you can change:
- `TITLE_INCLUDE` / `TITLE_EXCLUDE`: which titles count. Senior, lead, manager, intern and graduate roles are excluded by default.
- `SKILLS`: keywords from your resume and their weights in the score.
- `LOC_OK` / `LOC_PREFERRED`: target locations. Bengaluru and remote-India roles get a boost.
- `COMPANIES`: one entry per company.

## Coverage

These companies are pulled automatically from their own job systems: HRT, Tower, Jump, IMC, Da Vinci, DRW, Graviton, NK Securities, Squarepoint, QRT, Millennium, Jane Street, Citadel, Optiver, Salesforce, Walmart, Adobe, Microsoft, Amazon/AWS, Atlassian, Goldman Sachs, JPMorgan and NatWest.

The rest have no public job feed, so they're listed in the report with a link to check by hand: Google, LinkedIn, Flipkart, Amex, Jefferies, Accenture, D. E. Shaw, Samsung, the small Indian quant firms and others.

Added on 11 Oct: 59 more companies known to pay 30+ LPA for engineers with 5+ years' experience (Okta, Rubrik, Databricks, Point72, Deutsche Bank, CrowdStrike, ServiceNow, NVIDIA and more). 41 of them are pulled automatically; the rest are links.

The script now checks 112 companies, 71 of them automatically.

If a company changes its careers site, its row in "Feed health" turns red. The other feeds keep working.
