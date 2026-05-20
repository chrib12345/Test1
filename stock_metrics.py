#!/usr/bin/env python3
import sys
from datetime import datetime
import yfinance as yf

def fmt(val, prefix="", suffix="", decimals=2, na="N/A"):
    if val is None or val != val:  # NaN check
        return na
    try:
        return f"{prefix}{val:,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return na

def fmt_pct(val, na="N/A"):
    if val is None or val != val:
        return na
    try:
        return f"{val * 100:.2f}%"
    except (TypeError, ValueError):
        return na

def fmt_large(val, na="N/A"):
    if val is None or val != val:
        return na
    try:
        val = float(val)
        if val >= 1e12:
            return f"${val / 1e12:.2f}T"
        if val >= 1e9:
            return f"${val / 1e9:.2f}B"
        if val >= 1e6:
            return f"${val / 1e6:.2f}M"
        return f"${val:,.0f}"
    except (TypeError, ValueError):
        return na

def section(title):
    print(f"\n  {'─' * 40}")
    print(f"  {title}")
    print(f"  {'─' * 40}")

def row(label, value):
    print(f"  {label:<30} {value}")

def get_metrics(symbol):
    ticker = yf.Ticker(symbol.upper())
    info = ticker.info

    if not info or info.get("quoteType") is None:
        print(f"Symbol '{symbol.upper()}' not found. Please check the ticker and try again.")
        sys.exit(1)

    name = info.get("longName") or info.get("shortName") or symbol.upper()
    exchange = info.get("exchange", "")
    sector = info.get("sector", "N/A")
    industry = info.get("industry", "N/A")
    currency = info.get("currency", "USD")

    print("\n" + "═" * 46)
    print(f"  {name}  ({symbol.upper()})  —  {exchange}")
    print(f"  {sector}  ›  {industry}")
    print("═" * 46)

    section("PRICE")
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    prev_close = info.get("previousClose")
    change = None
    change_pct = None
    if price and prev_close:
        change = price - prev_close
        change_pct = change / prev_close
    row("Current Price", f"{currency} {fmt(price)}")
    row("Previous Close", f"{currency} {fmt(prev_close)}")
    if change is None:
        row("Day Change", "N/A")
    else:
        direction = "+" if change >= 0 else ""
        row("Day Change", f"{direction}{fmt(change)}  ({direction}{fmt_pct(change_pct)})")
    row("52-Week High", f"{currency} {fmt(info.get('fiftyTwoWeekHigh'))}")
    row("52-Week Low",  f"{currency} {fmt(info.get('fiftyTwoWeekLow'))}")
    row("50-Day MA",    f"{currency} {fmt(info.get('fiftyDayAverage'))}")
    row("200-Day MA",   f"{currency} {fmt(info.get('twoHundredDayAverage'))}")
    row("Beta",         fmt(info.get("beta")))

    section("VALUATION")
    row("Market Cap",         fmt_large(info.get("marketCap")))
    row("Enterprise Value",   fmt_large(info.get("enterpriseValue")))
    row("P/E (Trailing)",     fmt(info.get("trailingPE")))
    row("P/E (Forward)",      fmt(info.get("forwardPE")))
    row("PEG Ratio",          fmt(info.get("pegRatio")))
    row("Price / Book",       fmt(info.get("priceToBook")))
    row("Price / Sales",      fmt(info.get("priceToSalesTrailing12Months")))
    row("EV / Revenue",       fmt(info.get("enterpriseToRevenue")))
    row("EV / EBITDA",        fmt(info.get("enterpriseToEbitda")))

    section("FINANCIALS (TTM)")
    row("Revenue",            fmt_large(info.get("totalRevenue")))
    row("Gross Profit",       fmt_large(info.get("grossProfits")))
    row("EBITDA",             fmt_large(info.get("ebitda")))
    row("Net Income",         fmt_large(info.get("netIncomeToCommon")))
    row("EPS (Trailing)",     fmt(info.get("trailingEps"), prefix=currency + " "))
    row("EPS (Forward)",      fmt(info.get("forwardEps"),  prefix=currency + " "))
    row("Revenue Growth (YoY)", fmt_pct(info.get("revenueGrowth")))
    row("Earnings Growth (YoY)", fmt_pct(info.get("earningsGrowth")))

    section("MARGINS")
    row("Gross Margin",       fmt_pct(info.get("grossMargins")))
    row("Operating Margin",   fmt_pct(info.get("operatingMargins")))
    row("Profit Margin",      fmt_pct(info.get("profitMargins")))
    row("EBITDA Margin",      fmt_pct(info.get("ebitdaMargins")))

    section("RETURNS & EFFICIENCY")
    row("Return on Equity",   fmt_pct(info.get("returnOnEquity")))
    row("Return on Assets",   fmt_pct(info.get("returnOnAssets")))
    row("Revenue Per Share",  fmt(info.get("revenuePerShare")))

    section("BALANCE SHEET")
    row("Total Cash",         fmt_large(info.get("totalCash")))
    row("Total Debt",         fmt_large(info.get("totalDebt")))
    row("Cash Per Share",     fmt(info.get("totalCashPerShare"), prefix=currency + " "))
    row("Debt / Equity",      fmt(info.get("debtToEquity")))
    row("Current Ratio",      fmt(info.get("currentRatio")))
    row("Quick Ratio",        fmt(info.get("quickRatio")))

    section("CASH FLOW")
    row("Operating Cash Flow", fmt_large(info.get("operatingCashflow")))
    row("Free Cash Flow",      fmt_large(info.get("freeCashflow")))

    section("DIVIDENDS")
    row("Dividend Rate",      fmt(info.get("dividendRate"),  prefix=currency + " "))
    row("Dividend Yield",     fmt_pct(info.get("dividendYield")))
    row("Payout Ratio",       fmt_pct(info.get("payoutRatio")))
    ex_div = info.get("exDividendDate")
    ex_div_str = datetime.fromtimestamp(ex_div).strftime("%Y-%m-%d") if ex_div else "N/A"
    row("Ex-Dividend Date", ex_div_str)

    section("SHARES & OWNERSHIP")
    row("Shares Outstanding",  fmt_large(info.get("sharesOutstanding")).replace("$", ""))
    row("Float",               fmt_large(info.get("floatShares")).replace("$", ""))
    row("Insider Ownership",   fmt_pct(info.get("heldPercentInsiders")))
    row("Institution Ownership", fmt_pct(info.get("heldPercentInstitutions")))
    row("Short % of Float",    fmt_pct(info.get("shortPercentOfFloat")))

    print("\n" + "═" * 46 + "\n")


def main():
    if len(sys.argv) > 1:
        symbol = sys.argv[1]
    else:
        symbol = input("Enter stock symbol: ").strip()

    if not symbol:
        print("No symbol provided.")
        sys.exit(1)

    print(f"\nFetching data for {symbol.upper()}…")
    try:
        get_metrics(symbol)
    except Exception as e:
        print(f"\nError fetching data: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
