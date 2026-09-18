"""Scraper de Nationale Vacaturebank (nationalevacaturebank.nl, DPG Media).

Usa la API JSON publica (sin navegador):

    GET https://api.nationalevacaturebank.nl/api/jobs/v3/sites/nationalevacaturebank.nl/jobs
            ?query=<rol>&page=<n>&limit=<n>&sort=date

Devuelve las ofertas con descripcion, requisitos, salario, empresa, ubicacion...
Los textos se guardan en holandes; un paso posterior de traduccion (merge)
los convierte a ingles.
"""
