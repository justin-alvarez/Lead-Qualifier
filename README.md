# Lead Qualifier

CLI tool for qualifying HVAC/plumbing/mechanical company leads from ZoomInfo CSV exports. Built for Nuve's sales pipeline.

## Setup

```bash
pip install -r requirements.txt
```

Set your Anthropic API key (only needed for AI classification):
```bash
export ANTHROPIC_API_KEY=your_key_here
```

## Usage

```bash
# Basic run
python -m lead_qualifier -i companies.csv -o qualified.xlsx

# Custom thresholds
python -m lead_qualifier -i companies.csv -o qualified.xlsx \
  --min-revenue 5M --min-employees 10 --min-reviews 500

# Use config file
python -m lead_qualifier -i companies.csv -o qualified.xlsx --config config.yaml

# Test with small batch
python -m lead_qualifier -i companies.csv -o qualified.xlsx --limit 20

# Dry run (see what would happen)
python -m lead_qualifier -i companies.csv --dry-run

# Skip expensive gates
python -m lead_qualifier -i companies.csv -o qualified.xlsx --skip-reviews
python -m lead_qualifier -i companies.csv -o qualified.xlsx --skip-ai
python -m lead_qualifier -i companies.csv -o qualified.xlsx --service-type any

# Re-check previously rejected companies
python -m lead_qualifier -i companies.csv -o qualified.xlsx --recheck
python -m lead_qualifier -i companies.csv -o qualified.xlsx --recheck-after 90

# Use realtime API (faster, 2x cost)
python -m lead_qualifier -i companies.csv -o qualified.xlsx --realtime

# Regenerate dashboard from existing database
python -m lead_qualifier --dashboard-only

# Export ALL qualified leads from database
python -m lead_qualifier --export-all -o all_qualified.xlsx

# Database stats
python -m lead_qualifier --stats
```

## Pipeline

```
CSV Input → Dedup Check → Gate 1 (Revenue/Employees) → Gate 2 (Google Reviews) → Gate 3 (Website/AI) → Output
```

1. **Dedup**: Matches against persistent SQLite database by domain, ZoomInfo ID, or name+city+state
2. **Gate 1**: Revenue >= $2M and Employees >= 5 (configurable, from CSV data, FREE)
3. **Gate 2**: Google Reviews >= 200 (configurable, scraped from Google, FREE)
4. **Gate 3**: Website scrape + platform detection + Claude Haiku classification (~$0.001/company)

## Output

- **Excel workbook** with Qualified Leads, Manual Review, Rejected, and Summary sheets
- **HTML dashboard** with charts showing trends across runs (opens in any browser)
- **SQLite database** persists all data across runs

## Configuration

Create a `config.yaml` (see `config.example.yaml`):

```yaml
min_revenue: 2M
min_employees: 5
min_reviews: 200
service_type: residential
db: ./lead_qualifier.db
```

CLI flags always override config file values.
