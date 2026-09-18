"""Script temporal para inspeccionar la calidad de los datos extraidos."""
import pandas as pd

df = pd.read_csv("data/output/jobs.csv", encoding="utf-8-sig")
print("=== RESUMEN DE EXTRACCION (7 ofertas, Data Analyst Madrid) ===")
print()
for i, r in df.iterrows():
    print(f"--- Oferta {i+1}: {str(r.title)[:50]} ---")
    print(f"  Empresa:     {r.company_name}")
    print(f"  Ubicacion:   {r.location_raw}")
    print(f"  Modo:        {r.work_mode}")
    print(f"  Tipo:        {r.employment_type}")
    print(f"  Experiencia: {r.experience_level}")
    sal_raw = str(r.salary_raw)[:40] if pd.notna(r.salary_raw) and r.salary_raw else "N/A"
    if pd.notna(r.salary_min):
        sal = f"{r.salary_min}-{r.salary_max} {r.salary_currency}/{r.salary_period}"
    else:
        sal = "N/A"
    print(f"  Salario:     {sal}  (raw: {sal_raw})")
    print(f"  Postulados:  {r.num_applicants}")
    skills = str(r.skills)[:80] if pd.notna(r.skills) and r.skills else "N/A"
    print(f"  Skills:      {skills}")
    desc_len = len(str(r.description_full)) if pd.notna(r.description_full) else 0
    print(f"  Desc长度:    {desc_len} chars")
    print(f"  Industria:   {r.company_industry}")
    print(f"  Tamano:      {r.company_size}")
    print()
