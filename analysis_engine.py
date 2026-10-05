"""Free, deterministic service triage from recorded customer observations.

This assistant classifies a service route; it does not diagnose a component,
read photos, query manufacturer devices, or run a generative language model.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import server as profiles

VERSION = "1.1.0"
TYPES = ("Laadpaal", "Zonnepanelen", "Thuisbatterij", "Elektro")
UNKNOWN = {"", "onbekend", "anders/onbekend", "unknown", "weet niet", "niet bekend", "niet beschikbaar", "not available", "unavailable", "none", "geen", "n.v.t.", "nvt", "n/a", "?", "-", "—"}
NO_ERROR = {"geen", "nee", "none", "no", "0", "geen fout", "geen foutcode", "geen foutmelding", "no error", "no fault"}
LABELS = {
    "manufacturer": "Merk", "model": "Model", "serial": "Serienummer",
    "led": "LED/status", "vehicle_tested": "Auto laadt elders",
    "error_code": "Foutmelding", "app_online": "App/cloudstatus",
    "app_status": "Laadstatus in app", "schedule_active": "Laadschema actief",
    "equalizer_present": "Load balancing aanwezig", "last_successful": "Laatste goede werking",
    "production": "Productie", "monitoring_status": "Monitoringstatus",
    "red_led": "Rode LED", "green_led": "Groene LED", "blue_led": "Blauwe LED",
    "recent_event": "Gebeurtenis vóór de storing", "fault_led": "Fault-LED",
    "site_power": "Stroom aanwezig in gebouw", "soc": "Laadpercentage batterij",
    "firmware": "Firmwareversie", "bms_warning": "BMS-status",
    "breaker": "Automaat/aardlek status", "photo_present": "Foto toegevoegd",
}
SELECTS = {
    "led": ("rood", "groen", "blauw", "wit", "geel", "oranje", "uit"),
    "vehicle_tested": ("ja", "nee"), "app_online": ("online", "offline"),
    "monitoring_status": ("online", "offline"), "schedule_active": ("ja", "nee"),
    "equalizer_present": ("ja", "nee"), "fault_led": ("ja", "nee"),
    "site_power": ("ja", "nee"), "red_led": ("aan", "knippert", "uit"),
    "green_led": ("aan", "knippert", "uit"), "blue_led": ("aan", "knippert", "uit"),
}
TYPE_FIELDS = {
    "Laadpaal": ("led", "error_code", "app_online", "app_status", "vehicle_tested", "schedule_active", "equalizer_present", "last_successful"),
    "Zonnepanelen": ("production", "error_code", "monitoring_status", "fault_led", "red_led", "green_led", "blue_led", "site_power", "recent_event"),
    "Thuisbatterij": ("soc", "error_code", "monitoring_status", "bms_warning", "firmware", "recent_event"),
    "Elektro": ("breaker", "error_code", "recent_event"),
}
QUESTIONS = {
    "manufacturer": "Welk merk of welke fabrikant staat op het apparaat?",
    "model": "Wat is het exacte model?", "serial": "Wat is het serienummer?",
    "problem": "Wat gebeurt er en wanneer begon het probleem?",
    "led": "Welke kleur of welk patroon is zichtbaar, zonder het apparaat te openen?",
    "error_code": "Welke exacte foutmelding is zichtbaar, of wordt expliciet geen fout gemeld?",
    "app_online": "Is het laadpunt nu online of offline in de app?",
    "app_status": "Welke exacte laadstatus staat in de app?",
    "production": "Welke productie is zichtbaar, en was dit overdag bij verwachte productie?",
    "monitoring_status": "Is het systeem nu online of offline in monitoring?",
    "soc": "Welk laadpercentage van de batterij is zichtbaar?",
    "breaker": "Wat is zichtbaar aan de automaat of aardlek? Open of reset niets voor deze intake.",
}
NOTICE = "Voorbereidend advies op basis van de melding en bewaarde waarnemingen. De planner en vakbekwame monteur beoordelen de uitkomst."


def scalar(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ja" if value else "nee"
    if not isinstance(value, (str, int, float)):
        raise ValueError("Vul tekst of een keuze in bij de waarnemingen.")
    return str(value).strip()


def known(value):
    return scalar(value).casefold() not in UNKNOWN


def requirement_known(key, value):
    value = scalar(value)
    if key == "error_code" and value.casefold() in NO_ERROR:
        return True
    if not known(value):
        return False
    if key in ("app_online", "monitoring_status"):
        return value.casefold() in ("online", "offline")
    if key == "soc":
        return bool(re.fullmatch(r"\d+(?:[.,]\d+)?\s*(?:%|procent|percent)?", value.casefold()))
    return True


def canonical_brand(value):
    value = scalar(value)
    if not known(value):
        return "Onbekend"
    key = re.sub(r"[\s_-]+", "", value).casefold()
    return {"easee": "Easee", "solaredge": "SolarEdge", "goodwe": "GoodWe"}.get(key, value)


def clean_extra(extra):
    if extra is None:
        return {}
    if not isinstance(extra, dict):
        raise ValueError("De waarnemingen moeten als velden worden aangeleverd.")
    result = {}
    for key in LABELS:
        if key not in extra:
            continue
        value = scalar(extra[key])
        if len(value) > 500:
            raise ValueError(f"{LABELS[key]} is te lang (maximaal 500 tekens).")
        if value:
            result[key] = value
    if "manufacturer" in result:
        result["manufacturer"] = canonical_brand(result["manufacturer"])
    return result


def validate(t, text, extra=None):
    if t not in TYPES:
        raise ValueError("Kies Laadpaal, Zonnepanelen, Thuisbatterij of Elektro.")
    text = scalar(text)
    if not text or len(text) > 8000:
        raise ValueError("Vul een klachtomschrijving in van maximaal 8000 tekens.")
    cleaned = clean_extra(extra)
    soc = cleaned.get("soc", "")
    if known(soc) and re.fullmatch(r"-?\d+(?:[.,]\d+)?\s*(?:%|procent|percent)?", soc.casefold()):
        amount = float(re.sub(r"\s*(?:%|procent|percent)$", "", soc.casefold()).replace(",", "."))
        if not 0 <= amount <= 100:
            raise ValueError("Het laadpercentage van de batterij moet tussen 0 en 100 liggen.")
    return t, text, cleaned


def positive(text, pattern):
    """Find a reported symptom, respecting local negation and contrast clauses."""
    for clause in re.split(r"[.!?;·\n]|\b(?:maar|but|echter)\b", text.casefold()):
        for match in re.finditer(pattern, clause):
            before = clause[:match.start()]
            # 'geen rook of brandlucht' negates both; contrast begins a new clause.
            negations = list(re.finditer(r"\b(?:geen|zonder|no|without|niet|not)\b(?!\s+(?:alleen|only|uit te sluiten))", before))
            if negations:
                between = before[negations[-1].end():].strip()
                # Negation may carry through a list of symptoms, but not through
                # an unrelated statement ('geen productie, rook uit de kast').
                if not between or re.fullmatch(r"(?:(?:rook|smoke|brandlucht|burning smell|vonken|sparks|elektrische schok|brandschade|of|or|en|and)[\s,]*)+", between):
                    continue
            if re.match(r"\s+(?:niet|not)\b", clause[match.end():]):
                continue
            return True
    return False


def fault(t, text, extra):
    brand = canonical_brand(extra.get("manufacturer"))
    code = extra.get("error_code", "")
    code_valid = known(code) and code.casefold() not in NO_ERROR
    code_text = code if code_valid else ""
    corpus = " · ".join([text, code_text, extra.get("app_status", ""), extra.get("bms_warning", ""), extra.get("recent_event", "")])
    warnings = []
    evidence = []
    confidence = "laag"
    triage = "standaard"
    category = "Generieke technische storing"
    hazard = r"\b(?:rook|rookt|smoke|brandlucht|burning smell|vonken|sparks|elektrische schok|electric shock|brandschade|smelt(?:en|end|schade)?|smeult|opgezwollen batterij)\b"
    if positive(corpus, hazard):
        return {"category": "Mogelijk veiligheidsincident", "confidence": "hoog", "triage": "veiligheidsreview", "evidence": ["De melding bevat een niet-ontkende fysieke veiligheidsindicator."], "warnings": []}

    if brand == "Easee" and t == "Laadpaal":
        status = extra.get("app_status", "").casefold()
        red = extra.get("led", "").casefold().startswith("rood") or positive(text, r"\brode?\s+(?:led|lamp|licht)\b")
        if code_valid or status in ("error", "fout", "fault") or red:
            category, confidence, triage = "Laderfout / foutstatus", "middel", "technische review"
            evidence.append("Foutmelding of rode status heeft voorrang op een verbindingsmelding; oorzaak nog te beoordelen.")
            if code_valid:
                evidence.append("Vastgelegde foutmelding: " + code)
        elif positive(corpus, r"authenticat\w*|authori[sz]ation|rfid|autorisat\w*"):
            category, confidence = "Wachten op authenticatie", "middel"
            evidence.append("Gemelde appstatus of klacht verwijst naar toegangscontrole.")
        elif positive(corpus, r"\b(?:schedule\w*|laadschema|uitgesteld|schema)\b"):
            category, confidence = "Laadschema / wachten", "middel"
            evidence.append("Gemelde laadstatus verwijst naar een schema of wachtmoment.")
        elif positive(corpus, r"load balancing|equalizer|current limit|stroomlimiet|\bqueue\b"):
            category, confidence = "Load balancing / stroomlimiet", "middel"
            evidence.append("Gemelde status verwijst naar load balancing of een stroomlimiet.")
        elif extra.get("app_online", "").casefold() == "offline" or positive(corpus, r"\boffline\b"):
            category = "Offline / connectiviteit"
            confidence = "hoog" if extra.get("app_online", "").casefold() == "offline" else "middel"
            evidence.append("Offline-status gemeld; dit bewijst niet dat laden onmogelijk is.")
        elif extra.get("vehicle_tested", "").casefold() == "ja":
            category, confidence = "Geen laadstroom — laadpuntgericht", "middel"
            evidence.append("Volgens de waarneming laadt het voertuig elders; geen onderdeel als defect vastgesteld.")
        else:
            category = "Geen laadstroom — oorzaak nog open"
    elif brand in ("GoodWe", "SolarEdge") and t in ("Zonnepanelen", "Thuisbatterij"):
        matches = []
        families = [
            (r"\b(?:18x86|8x58|2x19)\b|isolati\w*|isolation(?: failure| fail| fault)?", "Isolatiefout" if brand == "SolarEdge" else "Isolation Failure — isolatie naar aarde", "veiligheidsreview"),
            (r"ground\s*(?:i\s*)?(?:failure|leakage)|aardlekfout", "Ground leakage / aardlekfout", "veiligheidsreview"),
            (r"pv\s*over\s*voltage|pv-?overspanning", "PV Over Voltage", "veiligheidsreview"),
            (r"over\s*temperature|oververhitting", "Over Temperature", "technische review"),
            (r"battery communication(?: failure)?|batterijcommunicati\w*", "Battery Communication Failure", "technische review"),
            (r"utility loss", "Utility Loss — net afwezig/onderbroken", "technische review"),
            (r"vac\s*fail(?:ure)?", "VAC Failure — netspanning buiten bereik", "technische review"),
            (r"fac\s*fail(?:ure)?", "FAC Failure — netfrequentie buiten bereik", "technische review"),
            (r"spi\s*failure", "Interne communicatiefout (SPI)", "technische review"),
        ]
        for pattern, label, level in families:
            if brand == "SolarEdge" and label not in ("Isolatiefout", "Over Temperature", "Battery Communication Failure"):
                continue
            if positive(corpus, pattern):
                matches.append((label, level, positive(code_text, pattern)))
        if matches:
            category, triage, in_code = matches[0]
            exact_code = bool(re.search(r"\b(?:18x86|8x58|2x19)\b", corpus.casefold())) and category == "Isolatiefout"
            confidence = "hoog" if in_code or exact_code else "middel"
            evidence.append(("Vastgelegde foutmelding: " + code) if in_code else "Gemelde foutfamilie: " + category)
            if len(matches) > 1:
                warnings.append("Meerdere foutfamilies gemeld. Controleer de actuele fout en tijdlijn; veiligheidsmeldingen krijgen voorrang.")
        elif code_valid:
            category = "Omvormerfout — exacte code aanwezig" if brand == "SolarEdge" else "GoodWe alarm/fout — overige"
            confidence, triage = "laag", "technische review"
            evidence.append("Code aanwezig, maar niet gekoppeld aan een bevestigde foutfamilie: " + code)
            warnings.append("Deze foutcode is niet inhoudelijk herkend. Verifieer model en code in fabrikantdocumentatie.")
        elif extra.get("monitoring_status", "").casefold() == "offline" or positive(corpus, r"\boffline\b|geen communicatie|no communication"):
            category = "Communicatiestoring" if brand == "SolarEdge" else "SEMS/SolarGo communicatiestoring"
            confidence = "hoog" if extra.get("monitoring_status", "").casefold() == "offline" else "middel"
            evidence.append("Communicatieprobleem gemeld; productie en werking afzonderlijk beoordelen.")
        elif brand == "SolarEdge" and positive(corpus, r"netspanning|netfrequentie|grid fault|grid voltage|grid frequency"):
            category, confidence, triage = "Net-/gridgerelateerde fout", "middel", "technische review"
            evidence.append("Melding verwijst naar netspanning of netfrequentie.")
        elif t == "Zonnepanelen" and (re.fullmatch(r"0(?:[.,]0+)?\s*(?:w|kw)?", extra.get("production", "").casefold()) or positive(corpus, r"geen productie|no production")):
            category, confidence = "Geen productie", "middel"
            evidence.append("Geen productie gemeld; tijdstip, daglicht en monitoring zijn nog van belang.")
        elif t == "Thuisbatterij":
            category = "Batterijstoring — oorzaak nog open"
        else:
            category = "Prestatie-/monitoringafwijking"
    elif t == "Thuisbatterij":
        category = "Batterijstoring — oorzaak nog open"
    elif t == "Elektro":
        triage = "technische review"

    for key in ("app_online", "monitoring_status"):
        if extra.get(key, "").casefold() == "online" and positive(text, r"\boffline\b"):
            warnings.append("De gekozen online-status en de offline-melding spreken elkaar tegen. Controleer de actuele status en het tijdstip.")
    if warnings and confidence == "hoog":
        confidence = "middel"
    if not evidence:
        evidence.append("Geen specifieke foutfamilie bevestigd uit de bewaarde informatie.")
    return {"category": category, "confidence": confidence, "triage": triage, "evidence": evidence, "warnings": warnings}


def route_knowledge(category, t="Elektro", brand="Onbekend"):
    if category == "Mogelijk veiligheidsincident":
        return {"service_route": "site-required", "competence": "Bevoegde vaktechnicus; veiligheid eerst beoordelen", "remote_checks": ["Exacte melding, tijdstip en locatie verzamelen zonder handelingen aan apparatuur"], "site_trigger": "Veiligheidsbeoordeling door de verantwoordelijke serviceorganisatie vóór gebruik of werkzaamheden.", "prep_categories": ["Melding en apparaatidentiteit", "Bedrijfsprocedure voor veiligheidsincidenten", "Passende bevoegdheid en veiligheidsmiddelen"], "escalation": "Direct menselijke veiligheidsreview; bij acuut gevaar de geldende noodprocedure volgen.", "source_title": "Veiligheidsreview — actuele modeldocumentatie vereist", "source_url": ""}
    if category in profiles.ROUTE_KNOWLEDGE:
        return profiles.route_knowledge(category)
    route = profiles.route_knowledge(category)
    if t in ("Zonnepanelen", "Thuisbatterij"):
        route["competence"] = "PV-/ESS-servicemedewerker; bevoegde technicus voor fysieke diagnose"
    if category in ("PV Over Voltage", "Over Temperature", "Interne communicatiefout (SPI)"):
        route.update(service_route="site-required" if category == "PV Over Voltage" else "remote-triage", competence="Bevoegde PV-/ESS-technicus", site_trigger="Menselijke technische review van de actuele fout vóór gebruik of werkzaamheden.", escalation="Fabrikantsupport en bevoegde technicus op basis van model, exacte fout en herhaling.")
    if brand in profiles.TECH_SOURCES:
        route["source_title"] = profiles.TECH_SOURCES[brand]["title"]
        route["source_url"] = profiles.TECH_SOURCES[brand]["url"]
    return route


def observations_for_case(case):
    result = {}
    facts = case.get("facts") or []
    for fact in facts if isinstance(facts, list) else []:
        if not isinstance(fact, str):
            continue
        for key, label in LABELS.items():
            if fact.startswith(label + ": "):
                result[key] = fact[len(label) + 2:]
                break
    for key, field in (("manufacturer", "manufacturer"), ("model", "model"), ("serial", "serial_no")):
        if case.get(field) is not None:
            result[key] = case[field]
    return clean_extra(result)


def assess(t, text, extra=None):
    t, text, extra = validate(t, text, extra)
    brand = canonical_brand(extra.get("manufacturer"))
    facts = ["Analyseversie: " + VERSION, "Klachtomschrijving vastgelegd"]
    for key, label in LABELS.items():
        if key in extra:
            facts.append(label + ": " + extra[key])
    required = ["problem"]
    if t != "Elektro":
        required += ["manufacturer", "model", "serial"]
    required += {"Laadpaal": ["led", "error_code"], "Zonnepanelen": ["production", "error_code"], "Thuisbatterij": ["soc", "error_code"], "Elektro": ["breaker"]}[t]
    if brand == "Easee" and t == "Laadpaal":
        required += ["app_online", "app_status"]
    if brand in ("SolarEdge", "GoodWe") and t in ("Zonnepanelen", "Thuisbatterij"):
        required.append("monitoring_status")
    missing_keys = [k for k in required if k != "problem" and not requirement_known(k, extra.get(k))]
    missing = [LABELS[k] for k in missing_keys]
    classified = fault(t, text, extra)
    route = route_knowledge(classified["category"], t, brand)
    ftf = profiles.ftf_knowledge(classified["category"])
    source = profiles.TECH_SOURCES.get(brand, {"title": "Fabrikantdocumentatie voor dit model vereist", "url": "", "api_targets": []})
    questions = [QUESTIONS.get(k, "Wat is de actuele " + LABELS[k].lower() + "?") for k in missing_keys]
    if classified["category"] == "Geen productie":
        questions.append("Was de nulproductie overdag bij verwachte productie? Controleer ook de productietijdlijn.")
    if classified["category"] == "Offline / connectiviteit":
        questions.append("Werkt laden nog wel terwijl de app offline is?")
    dispatch = {"Laadpaal": "Laadinfra serviceteam", "Zonnepanelen": "PV-serviceteam", "Thuisbatterij": "Batterij/energieopslag specialist", "Elektro": "Bevoegde elektromonteur"}[t]
    if classified["triage"] == "veiligheidsreview":
        dispatch = "Bevoegde technicus — veiligheidsreview vóór dispatch"
    elif classified["triage"] == "technische review":
        dispatch += " — technische review"
    warnings = list(classified["warnings"])
    if brand not in profiles.TECH_SOURCES:
        warnings.append("Geen specifiek ondersteund fabrikantprofiel. De exacte fout en modeldocumentatie moeten door het serviceteam worden beoordeeld.")
    fields = [{"key": k, "label": LABELS[k], "options": list(SELECTS.get(k, ())), "required": k in required} for k in TYPE_FIELDS[t]]
    return {
        "engine": "technische_kennisprofielen", "engine_version": VERSION, "notice": NOTICE,
        "type": t, "problem": text, "manufacturer": brand, "observations": extra,
        "facts": facts, "missing": missing, "score": round(100 * (len(required) - len(missing_keys)) / len(required)),
        "dispatch": dispatch, "prep": list(dict.fromkeys(profiles.prep_for(t, brand, extra, classified) + route["remote_checks"])),
        "fault_category": classified["category"], "fault_confidence": classified["confidence"],
        "triage_level": classified["triage"], "fault_evidence": classified["evidence"],
        "service_route": route["service_route"], "required_competence": route["competence"],
        "remote_checks": route["remote_checks"], "site_trigger": route["site_trigger"],
        "prep_categories": route["prep_categories"], "escalation_path": route["escalation"],
        "route_source_title": route["source_title"], "route_source_url": route["source_url"],
        "knowledge_title": source["title"], "knowledge_url": source["url"], "api_targets": source["api_targets"],
        "ftf_critical": ftf["critical_before_departure"], "ftf_gaps": ftf["common_avoidable_gap"], "ftf_parts": ftf["parts_categories"],
        "followup_questions": list(dict.fromkeys(questions)), "warnings": warnings, "input_fields": fields,
    }


PERSISTED = ("facts", "missing", "score", "dispatch", "prep", "fault_category", "fault_confidence", "triage_level", "fault_evidence", "service_route", "required_competence", "remote_checks", "site_trigger", "prep_categories", "escalation_path", "route_source_title", "route_source_url", "knowledge_title", "knowledge_url", "api_targets", "ftf_critical", "ftf_gaps", "ftf_parts")


def case_fields(analysis):
    return {k: analysis[k] for k in PERSISTED}


def analyze(t, text, extra=None):
    result = assess(t, text, extra)
    classified = {"category": result["fault_category"], "confidence": result["fault_confidence"], "triage": result["triage_level"], "evidence": result["fault_evidence"]}
    return result["facts"], result["missing"], result["score"], result["dispatch"], result["prep"], classified


def source_for(brand):
    return profiles.TECH_SOURCES.get(canonical_brand(brand), {"title": "Fabrikantdocumentatie voor dit model vereist", "url": "", "api_targets": []})


def for_case(case):
    result = assess(case["type"], case.get("problem"), observations_for_case(case))
    facts = case.get("facts") or []
    if not any(isinstance(f, str) and f.startswith("Analyseversie: ") for f in (facts if isinstance(facts, list) else [])):
        result["warnings"].append("Ouder dossier: oorspronkelijke vervolgantwoorden zijn mogelijk niet bewaard. Vul ontbrekende waarnemingen aan voor een vollediger advies.")
    return result


def self_check():
    samples = [
        ("Veiligheid bij Easee", "Laadpaal", "Rook en brandlucht uit de lader", {"manufacturer": "Easee", "app_online": "offline"}, "Mogelijk veiligheidsincident"),
        ("Veiligheid bij GoodWe", "Thuisbatterij", "Vonken uit de batterij", {"manufacturer": "GoodWe"}, "Mogelijk veiligheidsincident"),
        ("Ontkende rook", "Elektro", "Geen rook, geen brandlucht. Aardlek valt uit.", {}, "Generieke technische storing"),
        ("Offline laadpunt", "Laadpaal", "De app is offline", {"manufacturer": "Easee", "app_online": "offline"}, "Offline / connectiviteit"),
        ("Fout vóór offline", "Laadpaal", "Lader offline en rode LED", {"manufacturer": "Easee", "app_online": "offline", "led": "rood", "error_code": "RCD error"}, "Laderfout / foutstatus"),
        ("Autorisatie", "Laadpaal", "Waiting for authorization", {"manufacturer": "Easee"}, "Wachten op authenticatie"),
        ("Laadschema", "Laadpaal", "Pending scheduled charging", {"manufacturer": "Easee"}, "Laadschema / wachten"),
        ("Load balancing", "Laadpaal", "Equalizer current limit", {"manufacturer": "Easee"}, "Load balancing / stroomlimiet"),
        ("SolarEdge isolatie", "Zonnepanelen", "Geen productie", {"manufacturer": "SolarEdge", "error_code": "18x86", "monitoring_status": "offline"}, "Isolatiefout"),
        ("GoodWe isolatie", "Zonnepanelen", "Omvormer alarm", {"manufacturer": "GoodWe", "error_code": "Isolation Failure"}, "Isolation Failure — isolatie naar aarde"),
        ("GoodWe netverlies", "Zonnepanelen", "Omvormer alarm", {"manufacturer": "GoodWe", "error_code": "Utility Loss"}, "Utility Loss — net afwezig/onderbroken"),
        ("Batterijcommunicatie", "Thuisbatterij", "Alarm", {"manufacturer": "GoodWe", "bms_warning": "Battery Communication Failure"}, "Battery Communication Failure"),
        ("Geen fout is geen alarm", "Zonnepanelen", "Opbrengst beoordelen", {"manufacturer": "GoodWe", "error_code": "geen"}, "Prestatie-/monitoringafwijking"),
        ("Onbekende code", "Zonnepanelen", "Omvormer alarm", {"manufacturer": "SolarEdge", "error_code": "XYZ-987"}, "Omvormerfout — exacte code aanwezig"),
        ("Batterijcontext", "Thuisbatterij", "Batterij laadt niet", {"manufacturer": "GoodWe", "soc": 0}, "Batterijstoring — oorzaak nog open"),
        ("Nulproductie", "Zonnepanelen", "Nulproductie overdag", {"manufacturer": "GoodWe", "production": "0 W"}, "Geen productie"),
    ]
    started = time.perf_counter()
    checks = []
    for name, t, text, extra, expected in samples:
        try:
            result = assess(t, text, extra)
            passed = result["fault_category"] == expected
        except Exception:
            passed = False
        checks.append({"name": name, "passed": passed})
    return {"healthy": all(c["passed"] for c in checks), "engine": "Technische kennisprofielen", "version": VERSION, "checked_at": datetime.now(timezone.utc).isoformat(), "passed": sum(c["passed"] for c in checks), "total": len(checks), "duration_ms": round((time.perf_counter() - started) * 1000, 1), "external_model": False, "extra_cost": 0, "checks": checks}
