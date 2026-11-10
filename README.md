# International Cost of Capital

A Streamlit classroom app that compares the same project's cost of equity, pre-tax cost of debt and WACC in USD and a selected country's currency. The default project uses Software (System & Application), United States, the U.S. statutory tax rate, a 6.5% equity risk premium and 30% debt financing.

## Deploy on Streamlit Community Cloud

1. Create a GitHub repository and commit `app.py`, `requirements.txt` and `README.md` to its root.
2. Open [Streamlit Community Cloud](https://share.streamlit.io/) and create an app from that repository.
3. Choose the branch containing the files (usually `main`) and enter `app.py` as the entry point. Choose Python 3.12 in Advanced settings and deploy. The app uses public sources and does not require secrets.
4. Keep the GitHub repository URL and the published app URL for your submission.

For a local run with Python 3.12, install `requirements.txt` with pip and run `streamlit run app.py`.

## Data and overrides

- [Damodaran US industry workbook](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls). The app uses the **Unlevered beta** column. If the live workbook is unavailable, it uses the supplied workbook snapshot dated 2026-01-05.
- [Damodaran credit spreads](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html). The app reads Baa2/BBB from the large non-financial firm column.
- [OECD corporate tax data](https://www.oecd.org/en/data/datasets/corporate-income-tax-rates-database.html). The app requests the latest combined statutory corporate income tax rate per country from Data Explorer. If that endpoint is unavailable, it downloads the [2026 figure workbook](https://stat.link/mstq82) and labels the year and source.
- [Trading Economics 10-year government bond yields](https://tradingeconomics.com/bonds). The app matches the country by its country code and preserves the quote date as displayed by the source.
- Refresh clears the app's one-hour source cache. The source panel reports fetch status and time. If a source is unavailable, the app labels and uses saved quotes where available. The backup yield and tax quotes cover the U.S. and the U.K. Enter any missing inputs manually. Manual inputs for rates use percentage points, so enter 4 for 4%.

## Model assumptions

The course equations were unavailable. This implementation uses CAPM and the usual tax-adjusted WACC, with the following assumptions. Each currency's 10-year government yield proxies for its benchmark rate. One project tax rate, debt share and equity risk premium apply to both currencies. The model makes no separate adjustment for sovereign default or market liquidity.

For debt weight `wD`, calculate `D/E = wD / (1 − wD)` and `betaL = betaU × [1 + (1 − tax) × D/E]`. For each currency use `Ke = yield + betaL × ERP`, `Kd = yield + BBB spread` and `WACC = (1 − wD) × Ke + wD × Kd × (1 − tax)`.

The default foreign spread is **USD spread × (1 + local yield) / (1 + USD yield)**, with all inputs in decimals. This follows from multiplying the USD gross borrowing rate by the ratio of the two gross government yields and subtracting the local benchmark. The automatic spread is a model estimate. Use the target-spread override to compare a separately supplied local spread. The override affects only target-currency results.

The all-industry table uses each industry's unlevered beta under the same financing assumptions. Switch between WACC, cost of equity and pre-tax cost of debt. A manual beta override applies to the selected industry. The CSV includes all three measures for both currencies along with their inputs and source dates.

## Reproduce a calculation

Use the United Kingdom as the target country and keep U.S. project tax selected. Override the unlevered beta to **1.20**, the project tax to **25%**, the USD BBB spread to **1.00%**, and the U.S. and target yields to **4.00%** and **6.00%**. Keep debt at 30%, ERP at 6.5% and the target spread override off.

- Levered beta is 1.20 × [1 + 0.75 × (0.30 / 0.70)] = 1.5857142857.
- USD cost of equity is 4% + 1.5857142857 × 6.5% = 14.3071428571%. USD pre-tax debt costs 5.00%. USD WACC is 0.70 × 14.3071428571% + 0.30 × 5.00% × 0.75 = **11.14%**.
- The model's target spread is 1.00% × 1.06 / 1.04 ≈ 1.0192307692%. Target cost of equity is 16.3071428571% and target pre-tax debt costs 7.0192307692%. Target WACC rounds to **12.99%**.

Then test equal 10-year yields with the target spread override off, zero debt financing, and a target spread override with 30% debt. Equal yields should give equal results. Zero debt should give WACC equal to cost of equity. The spread override should leave USD results unchanged.
