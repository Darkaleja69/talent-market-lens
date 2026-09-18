"""Resume final de la extraccion completa."""
import pandas as pd

df = pd.read_csv("data/output/jobs.csv", encoding="utf-8-sig")
print(f"=== EXTRACCION COMPLETA: {len(df)} ofertas unicas ===")
print()

# Por ciudad
print("Por ciudad:")
print(df["search_city"].value_counts().to_string())
print()

# Por rol
print("Por rol buscado:")
print(df["search_role"].value_counts().to_string())
print()

# Cobertura de campos
print("Cobertura de campos extraidos:")
for col in ["title", "company_name", "location_raw", "work_mode",
            "employment_type", "description_full", "num_applicants",
            "company_url", "company_size", "salary_raw", "posted_relative"]:
    if col in df.columns:
        non_empty = df[col].apply(lambda x: pd.notna(x) and str(x).strip() != "").sum()
        pct = non_empty / len(df) * 100
        print(f"  {col:25s}: {non_empty:3d}/{len(df)} ({pct:.0f}%)")

print()
# Work mode distribution
print("Distribucion modo de trabajo:")
print(df["work_mode"].value_counts(dropna=False).to_string())
print()

# Top empresas
print("Top 10 empresas:")
print(df["company_name"].value_counts().head(10).to_string())
print()

# Longitud descripcion
desc_lens = df["description_full"].apply(lambda x: len(str(x)) if pd.notna(x) and str(x).strip() else 0)
print(f"Descripcion: media={desc_lens.mean():.0f} chars, min={desc_lens.min()}, max={desc_lens.max()}")
print()

# Fuente
print("Fuente de datos:")
print(df["source"].value_counts().to_string())
