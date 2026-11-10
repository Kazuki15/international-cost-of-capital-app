"""Compare the same project's cost of capital in USD and another currency."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO, StringIO
import re
from zipfile import BadZipFile

import pandas as pd
import pycountry
import requests
import streamlit as st
import xlrd
from lxml import etree, html

BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
SPREAD_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
YIELDS_URL = "https://tradingeconomics.com/bonds"
TAX_URL = (
    "https://sdmx.oecd.org/public/rest/v1/data/"
    "OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,2.0/.A.CIT_C.ST..S13..."
)
TAX_STATLINK = "https://stat.link/mstq82"
TAX_PAGE = "https://www.oecd.org/en/data/datasets/corporate-income-tax-rates-database.html"
DEFAULT_INDUSTRY = "Software (System & Application)"
METRICS = {
    "WACC (%)": "WACC",
    "Equity cost (%)": "Cost of equity",
    "Pre-tax debt cost (%)": "Pre-tax cost of debt",
}
SNAPSHOT_YIELDS = {
    "USA": (4.9840, "2026-11-10 · Trading Economics snapshot"),
    "GBR": (5.2058, "2026-11-10 · Trading Economics snapshot"),
}
SNAPSHOT_TAXES = {
    "USA": (25.52849983, "2026 · OECD Figure 2.1 snapshot"),
    "GBR": (25.0, "2026 · OECD Figure 2.1 snapshot"),
}
SNAPSHOT_SPREAD = (1.11, "January 2026 · Damodaran snapshot")


def download(url, **kwargs):
    response = requests.get(url, timeout=(6, 12), **kwargs)
    response.raise_for_status()
    return response


def read_betas(content):
    workbook = xlrd.open_workbook(file_contents=content)
    for sheet in workbook.sheets():
        for index in range(sheet.nrows):
            labels = [str(cell).strip().casefold() for cell in sheet.row_values(index)]
            if "industry name" not in labels or "unlevered beta" not in labels:
                continue
            names = sheet.col_values(labels.index("industry name"), start_rowx=index + 1)
            betas = sheet.col_values(labels.index("unlevered beta"), start_rowx=index + 1)
            values = {
                name.strip(): beta
                for name, beta in zip(names, betas)
                if isinstance(name, str) and name.strip() and isinstance(beta, (float, int))
            }
            if DEFAULT_INDUSTRY in values:
                date = "Publication date not listed"
                for row in range(index):
                    if str(sheet.cell_value(row, 0)).strip().casefold() == "date updated:":
                        raw_date = sheet.cell_value(row, 1)
                        if isinstance(raw_date, (float, int)):
                            date = xlrd.xldate_as_datetime(raw_date, workbook.datemode).date().isoformat()
                        elif raw_date:
                            date = str(raw_date)
                        break
                return values, date
    raise ValueError("Industry Name and Unlevered Beta were not found in the workbook.")


def read_spread():
    page = html.fromstring(download(SPREAD_URL).content)
    for row in page.xpath("//tr"):
        cells = [" ".join(cell.text_content().split()) for cell in row.xpath("./td")]
        if len(cells) >= 4 and cells[2] == "Baa2/BBB":
            notes = " ".join(page.xpath("//p[contains(., 'Date of Analysis')]")[0].text_content().split())
            return float(cells[3].rstrip("%")), notes.removeprefix("Date of Analysis:").strip()
    raise ValueError("The large non-financial Baa2/BBB row was not found.")


def read_yields():
    page = html.fromstring(download(YIELDS_URL, headers={"User-Agent": "Mozilla/5.0"}).content)
    result = {}
    for row in page.xpath("//tr[td[@id='p'] and td[@id='date']]"):
        links = row.xpath("./td/a[contains(@href, '/government-bond-yield')]")
        flags = row.xpath(".//div[contains(@class, 'flag-')]/@class")
        if not links or not flags:
            continue
        match = re.search(r"\bflag-([a-z]{2})\b", flags[0])
        country = pycountry.countries.get(alpha_2=match[1].upper()) if match else None
        if not country:
            continue
        value = row.xpath("./td[@id='p']")[0].text_content().strip().strip("%")
        date = row.xpath("./td[@id='date']")[0].text_content().strip()
        try:
            result.setdefault(country.alpha_3, (float(value.replace(",", "")), date))
        except ValueError:
            continue
    if "USA" not in result:
        raise ValueError("The U.S. 10-year quote was not found.")
    return result


def read_taxes():
    try:
        response = download(
            TAX_URL,
            params={"startPeriod": "2024", "dimensionAtObservation": "AllDimensions"},
            headers={"Accept": "text/csv"},
        )
        rows = pd.read_csv(StringIO(response.text), usecols=["REF_AREA", "TIME_PERIOD", "OBS_VALUE"])
        latest = rows.dropna().sort_values("TIME_PERIOD").drop_duplicates("REF_AREA", keep="last")
        values = {
            row.REF_AREA: (float(row.OBS_VALUE), f"{row.TIME_PERIOD} · OECD Data Explorer")
            for row in latest.itertuples()
        }
        if "USA" in values:
            return values
    except (requests.RequestException, ValueError):
        pass

    workbook = pd.read_excel(BytesIO(download(TAX_STATLINK).content), header=None, engine="openpyxl")
    values = {}
    for code, rate in workbook.iloc[:, :2].itertuples(index=False, name=None):
        if isinstance(code, str) and re.fullmatch(r"[A-Z]{3}", code) and isinstance(rate, (int, float)):
            if pd.notna(rate):
                values[code] = (100 * rate, "2026 · OECD Figure 2.1 StatLink")
    if "USA" not in values:
        raise ValueError("The U.S. statutory tax rate was not found.")
    return values


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_sources():
    sources = {
        "Industry betas": lambda: read_betas(download(BETAS_URL).content),
        "USD BBB spread": read_spread,
        "10-year yields": read_yields,
        "Corporate tax": read_taxes,
    }
    data, errors = {}, {}
    with ThreadPoolExecutor(max_workers=len(sources)) as executor:
        jobs = {name: executor.submit(fetch) for name, fetch in sources.items()}
        for name, job in jobs.items():
            try:
                data[name] = job.result()
            except (requests.RequestException, ValueError, IndexError, OSError,
                    BadZipFile, xlrd.XLRDError, etree.ParserError) as error:
                errors[name] = str(error)
    return data, errors, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def currency_spread(usd_spread, usd_yield, local_yield):
    """All inputs and outputs are decimal rates."""
    return usd_spread * (1 + local_yield) / (1 + usd_yield)


def calculate(beta, tax, debt_share, erp, gov_yield, spread):
    levered_beta = beta * (1 + (1 - tax) * debt_share / (1 - debt_share))
    equity = gov_yield + levered_beta * erp
    debt = gov_yield + spread
    wacc = (1 - debt_share) * equity + debt_share * debt * (1 - tax)
    return levered_beta, equity, debt, wacc


def rate_input(label, quote, key, *, minimum=-99.99, maximum=None, manual_if_missing=True):
    value, date = quote if quote is not None else (None, "")
    override = st.checkbox(
        f"Override {label.lower()}", value=value is None and manual_if_missing, key=f"{key}_manual"
    )
    if override:
        result = st.number_input(
            label, value=value, min_value=minimum, max_value=maximum,
            step=0.05, format="%.4f", key=key
        )
        return result, "Manual input"
    st.write(f"**{label}** · {value:.4f}%" if value is not None else f"**{label}** · unavailable")
    return value, date


def app():
    st.set_page_config(page_title="International Cost of Capital", layout="wide")
    st.title("International Cost of Capital")
    st.write("Compare one project's financing costs in U.S. dollars and a selected country's currency.")
    if st.button("Refresh source data"):
        fetch_sources.clear()
    with st.spinner("Checking market and tax data…"):
        data, errors, fetched = fetch_sources()

    beta_data, beta_date = data.get("Industry betas", (ATTACHED_BETAS, ATTACHED_BETA_DATE))
    yields = data.get("10-year yields", SNAPSHOT_YIELDS)
    taxes = data.get("Corporate tax", SNAPSHOT_TAXES)
    spread_quote = data.get("USD BBB spread", SNAPSHOT_SPREAD)
    if errors:
        st.warning("Some sources could not be refreshed. Dated snapshots are used where available. "
                   "Review the source panel or enter values manually.")
    with st.expander("Sources and data dates"):
        for name, link in [
            ("Industry betas", BETAS_URL), ("USD BBB spread", SPREAD_URL),
            ("10-year yields", YIELDS_URL), ("Corporate tax", TAX_PAGE),
        ]:
            status = "Using saved snapshot where available" if name in errors else "Retrieved"
            st.markdown(f"[{name}]({link}) · {status}")
            if name in errors:
                st.caption(errors[name])
        st.caption(f"Fetch time: {fetched}. Bond dates are shown as reported by the source.")
        st.caption(f"Industry beta reference: {beta_date}")
        st.markdown(f"[OECD 2026 StatLink fallback]({TAX_STATLINK})")

    names = sorted(beta_data)
    countries = {country.alpha_3: country.name for country in pycountry.countries}
    countries.update(GBR="United Kingdom", KOR="South Korea", TWN="Taiwan", TUR="Türkiye")
    country_codes = ["USA"] + sorted(set(countries) - {"USA"}, key=countries.get)
    left, right = st.columns(2)
    with left:
        industry = st.selectbox("Industry", names, index=names.index(DEFAULT_INDUSTRY))
        erp = st.number_input("Equity risk premium (%)", 0.0, 100.0, 6.5, 0.1) / 100
    with right:
        country = st.selectbox("Target country", country_codes, format_func=countries.get)
        debt_share = st.number_input("Target debt financing (%)", 0.0, 99.99, 30.0, 1.0) / 100

    tax_choice = st.radio("Project tax rate used in both currencies", ["United States", "Target country"],
                          horizontal=True)
    tax_country = "USA" if tax_choice == "United States" else country
    us, local, project = st.columns(3)
    with us:
        st.subheader("U.S. inputs")
        usd_yield, usd_yield_date = rate_input("U.S. 10-year yield", yields.get("USA"), "usd_yield")
        usd_spread, usd_spread_date = rate_input("USD BBB spread", spread_quote, "usd_spread", minimum=0.0)
    with local:
        st.subheader(f"{countries[country]} inputs")
        if country == "USA":
            usd_quote = (usd_yield, usd_yield_date) if usd_yield is not None else None
            local_yield, local_yield_date = rate_input("Target 10-year yield", usd_quote, "local_yield_USA")
        else:
            local_yield, local_yield_date = rate_input(
                "Target 10-year yield", yields.get(country), f"local_yield_{country}"
            )
        auto_spread = None
        if all(value is not None for value in (usd_spread, usd_yield, local_yield)):
            auto_spread = currency_spread(usd_spread / 100, usd_yield / 100, local_yield / 100) * 100
        auto_quote = (auto_spread, "Model estimate") if auto_spread is not None else None
        local_spread, local_spread_source = rate_input(
            "Target BBB spread", auto_quote, f"local_spread_{country}", minimum=0.0, manual_if_missing=False
        )
    with project:
        st.subheader("Shared project inputs")
        tax, tax_date = rate_input(
            f"{countries[tax_country]} tax rate", taxes.get(tax_country), f"tax_{tax_country}",
            minimum=0.0, maximum=100.0
        )
        beta_override = st.checkbox("Override industry unlevered beta", key=f"beta_override_{industry}")
        beta = (
            st.number_input("Unlevered beta", value=float(beta_data[industry]), step=0.01,
                            format="%.4f", key=f"beta_{industry}")
            if beta_override else beta_data[industry]
        )
        st.write(f"**Unlevered beta** · {beta:.4f}")

    with st.expander("Model assumptions"):
        st.markdown(
            "Debt and equity weights use the target debt share. The same project tax rate and ERP apply "
            "to both currencies. The 10-year government yields proxy for currency-specific benchmark "
            "rates, with no separate sovereign adjustment. The foreign BBB spread is an estimate "
            "based on relative gross benchmark rates."
        )
        st.latex(r"\beta_L=\beta_U[1+(1-T)D/E],\quad D/E=w_D/(1-w_D)")
        st.latex(r"s_{local}=s_{USD}\frac{1+y_{local}}{1+y_{USD}}")
        st.latex(r"k_e=y+\beta_L ERP,\quad k_d=y+s,\quad WACC=(1-w_D)k_e+w_Dk_d(1-T)")
        st.caption("This assignment model uses explicit assumptions because course formulas were unavailable.")

    if any(value is None for value in (tax, usd_yield, local_yield, usd_spread, local_spread)):
        st.info("Enter any missing rates above to see the comparison.")
        return

    tax_rate = tax / 100
    scenarios = [
        ("USD", usd_yield, usd_spread, usd_yield_date, usd_spread_date),
        (f"Local · {countries[country]}", local_yield, local_spread, local_yield_date, local_spread_source),
    ]

    def result_rows(name, selected_beta):
        rows = []
        for currency, gov, spread, quote_date, spread_source in scenarios:
            levered, equity, debt, wacc = calculate(
                selected_beta, tax_rate, debt_share, erp, gov / 100, spread / 100
            )
            rows.append({
                "Industry": name, "Country": countries[country], "Currency basis": currency,
                "Unlevered beta": selected_beta, "Levered beta": levered,
                "Beta date or input": "Manual input" if name == industry and beta_override else beta_date,
                "10-year yield (%)": gov, "Yield date or input": quote_date,
                "BBB spread (%)": spread, "Spread date or input": spread_source,
                "Tax country": countries[tax_country], "Tax rate (%)": tax, "Tax year or input": tax_date,
                "ERP (%)": erp * 100, "Debt share (%)": debt_share * 100,
                "Equity cost (%)": equity * 100, "Pre-tax debt cost (%)": debt * 100, "WACC (%)": wacc * 100,
                "Data fetch (UTC)": fetched,
            })
        return rows

    st.subheader("Selected industry results")
    selected = result_rows(industry, beta)
    for column, row in zip(st.columns(2), selected):
        with column:
            st.markdown(f"**{row['Currency basis']}**")
            for metric in ("Equity cost (%)", "Pre-tax debt cost (%)", "WACC (%)"):
                st.metric(METRICS[metric], f"{row[metric]:.2f}%")
            st.caption(f"10-year yield: {row['Yield date or input']} · BBB: {row['Spread date or input']}")
    st.caption(f"Tax input: {tax_date}. Levered beta: {selected[0]['Levered beta']:.4f}.")
    st.subheader("All-industry comparison")
    comparison = pd.DataFrame(
        row for name in names for row in result_rows(name, beta if name == industry else beta_data[name])
    )
    comparison_metric = st.radio("Compare across industries", list(METRICS), horizontal=True)
    table = comparison.pivot(index="Industry", columns="Currency basis", values=comparison_metric)
    st.dataframe(table[[scenario[0] for scenario in scenarios]].round(3), width="stretch")
    st.download_button("Download all results (CSV)", comparison.to_csv(index=False),
                       "international-cost-of-capital.csv", "text/csv")


# Course-provided workbook snapshot, used only when the live workbook is unavailable.
ATTACHED_BETA_DATE = '2026-01-05 · supplied Damodaran workbook'
ATTACHED_BETAS = {'Advertising': 0.930085673859911,
 'Aerospace/Defense': 0.846668742124111,
 'Air Transport': 0.7040501862693873,
 'Apparel': 0.7579820882045375,
 'Auto & Truck': 1.2690329072913427,
 'Auto Parts': 1.0217137832780625,
 'Bank (Money Center)': 0.3410587595479105,
 'Banks (Regional)': 0.2865156418030888,
 'Beverage (Alcoholic)': 0.6131715419678557,
 'Beverage (Soft)': 0.5556711918551647,
 'Broadcasting': 0.28636169661672445,
 'Brokerage & Investment Banking': 0.5808487698048416,
 'Building Materials': 0.9301623477287291,
 'Business & Consumer Services': 0.7735875598670026,
 'Cable TV': 0.3523921209643926,
 'Chemical (Basic)': 0.5800328658623819,
 'Chemical (Diversified)': 0.36655193179254153,
 'Chemical (Specialty)': 0.7922271711710575,
 'Coal & Related Energy': 1.0165349872490315,
 'Computer Services': 0.9155102559970569,
 'Computers/Peripherals': 1.3050624188250832,
 'Construction Supplies': 1.0160678372067562,
 'Diversified': 0.7889302024061987,
 'Drugs (Biotechnology)': 1.034128692045902,
 'Drugs (Pharmaceutical)': 0.8862112018778732,
 'Education': 0.6601955817730791,
 'Electrical Equipment': 1.1477945494288007,
 'Electronics (Consumer & Office)': 0.8298525821858579,
 'Electronics (General)': 0.8973653021365886,
 'Engineering/Construction': 1.094912241925567,
 'Entertainment': 0.7371838269272858,
 'Environmental & Waste Services': 0.814469065497658,
 'Farming/Agriculture': 0.8130198178522071,
 'Financial Svcs. (Non-bank & Insurance)': 0.3188823716863237,
 'Food Processing': 0.45741810112318915,
 'Food Wholesalers': 0.6408819228030607,
 'Furn/Home Furnishings': 0.6247880689027374,
 'Green & Renewable Energy': 0.46330103394106914,
 'Healthcare Products': 0.829063339182968,
 'Healthcare Support Services': 0.6889158973179456,
 'Heathcare Information and Technology': 0.9913008450033202,
 'Homebuilding': 0.7849604569802948,
 'Hospitals/Healthcare Facilities': 0.5517295035260499,
 'Hotel/Gaming': 0.8322513645728964,
 'Household Products': 0.7178159032286779,
 'Information Services': 0.7371769598623608,
 'Insurance (General)': 0.5636395230036526,
 'Insurance (Life)': 0.4271563388858468,
 'Insurance (Prop/Cas.)': 0.43565645519640217,
 'Investments & Asset Management': 0.5297158729912839,
 'Machinery': 0.8679764970807725,
 'Metals & Mining': 0.963642813760502,
 'Office Equipment & Services': 0.980603950937201,
 'Oil/Gas (Integrated)': 0.2712192455295165,
 'Oil/Gas (Production and Exploration)': 0.562759894026207,
 'Oil/Gas Distribution': 0.4650757918129057,
 'Oilfield Svcs/Equip.': 0.7430388962534321,
 'Packaging & Container': 0.7230693503118022,
 'Paper/Forest Products': 0.7215533176034462,
 'Power': 0.31002381536217716,
 'Precious Metals': 0.7932430128835662,
 'Publishing & Newspapers': 0.47730712965508676,
 'R.E.I.T.': 0.39290436962561487,
 'Real Estate (Development)': 0.4782878873556669,
 'Real Estate (General/Diversified)': 0.57785949402086,
 'Real Estate (Operations & Services)': 0.8148915126864962,
 'Recreation': 0.6950913817523879,
 'Reinsurance': 0.43840910722706555,
 'Restaurant/Dining': 0.767419655502052,
 'Retail (Automotive)': 0.6984325625385885,
 'Retail (Building Supply)': 1.3070143643127536,
 'Retail (Distributors)': 0.7826829040437357,
 'Retail (General)': 0.7599637023048608,
 'Retail (Grocery and Food)': 0.804751465127235,
 'Retail (REITs)': 0.4361730155949174,
 'Retail (Special Lines)': 0.9487830646597303,
 'Rubber& Tires': 0.14353369356004206,
 'Semiconductor': 1.48925822268736,
 'Semiconductor Equip': 1.3480733569303134,
 'Shipbuilding & Marine': 0.644012905556584,
 'Shoe': 0.9336746513867349,
 'Software (Entertainment)': 1.0127806880948524,
 'Software (Internet)': 1.5460643073174678,
 'Software (System & Application)': 1.225391886520662,
 'Steel': 0.9035142702396447,
 'Telecom (Wireless)': 0.3872590553991877,
 'Telecom. Equipment': 0.8634913488563035,
 'Telecom. Services': 0.3654342533759909,
 'Tobacco': 0.6775903166712723,
 'Total Market': 0.7217298803492256,
 'Total Market (without financials)': 0.8775172986449091,
 'Transportation': 0.67531101084817,
 'Transportation (Railroads)': 0.8069481235657714,
 'Trucking': 0.8504180575613394,
 'Utility (General)': 0.14861284002770483,
 'Utility (Water)': 0.281177328534438}


if __name__ == "__main__":
    app()
