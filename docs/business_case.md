# Business case — where the car-model identifier earns money

Full research dossier (sources, market model, five platform concepts, complete financials):
**Tekshir Dossier** — https://claude.ai/code/artifact/1b33542a-0b83-4689-8b19-a8d5f48c8f76

## The one-paragraph version

Uzbekistan's used-car market moved ~1.16M cars worth **$11.2B in 2023** (KPMG), half of them
listed on Avtoelon/OLX — yet the country has **no vehicle-history service, no organized
inspection market, and no price reference**. Russia, Kazakhstan, Pakistan and the EU all grew
profitable trust layers on identical foundations (Autoteka, Aster Check, PakWheels CarSure,
carVertical — which covers 28 countries but not Uzbekistan). The recommended venture,
**Tekshir**, sells mobile inspections (299k сум) and instant history reports (49k сум) to
buyers, subscriptions to dealers, and a valuation/verification API to the 23 banks whose
regulator now risk-weights auto loans by collateral accuracy. Base case (formula-driven
workbook, recalculated): **$1.45M revenue in year 3 at ~68% gross margin, monthly break-even
~month 24, cumulative cash-positive ~month 36, $400k seed** against a ~$240k trough.

## Where the capstone model fits

| Capstone asset | Role in the product |
|---|---|
| 30–40-class UZ model classifier | Photo-vs-claim verification on every report; trim features for valuation |
| Scraped listing corpus (photos + prices, listing-id splits) | Training set for the price model; seed of the listing archive |
| Collection pipeline (polite, resumable, audited) | The archive builder that compounds into the data moat |

## Five concepts considered (scored in the dossier)

1. **Tekshir** — inspection + history + valuation platform → **the pick (27/30)**
2. Narx API — valuation-as-a-service for banks/insurers (best as Tekshir phase 2)
3. SotibOl — C2B instant offer + dealer auction (right later, on top of inspection supply)
4. AvtoKredit24 — embedded used-car F&I (attach, not standalone)
5. DilerPro — dealer inventory SaaS (a feature, not a company)

Key verified numbers used throughout: KPMG «Анализ рынка автотранспортных средств в РУз»
(Oct 2024), ЦБ РУз lending data, Kolesa Group / AIM Group revenue disclosures, Нацкомстат
wages, carVertical odometer-fraud studies, PakWheels/Autoteka/Carfax pricing.
