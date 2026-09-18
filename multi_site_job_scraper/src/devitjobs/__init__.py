"""Scraper de DevITJobs.nl (bolsa de empleo IT de Paises Bajos).

A diferencia del resto de sitios, DevITJobs expone una API JSON publica
(sin navegador ni challenge):

    GET https://devitjobs.nl/api/jobsLight      -> lista de ofertas (resumen)
    GET https://devitjobs.nl/api/job/<_id>      -> detalle completo (descripcion,
                                                    requisitos, responsabilidades,
                                                    salario, perks, ...)

El objetivo es obtener el maximo de campos posible para enriquecer los
datos de Holanda.
"""
