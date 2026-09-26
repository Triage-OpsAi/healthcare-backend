"""Versioned documentation configuration. Fields are documentation, never treatment rules."""
import re

VERSION = "wardvoice-1"
TEMPLATES = {}

def add(specialty, key, title, encounter_type, fields, required, destination="clinical", stage=""):
    labels = fields.split("|")
    TEMPLATES[key] = dict(id=key, specialty=specialty, title=title, encounter_type=encounter_type,
        stage=stage or encounter_type, destination=destination, version=VERSION,
        fields=[dict(key=re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_"), label=label,
                     required=label in required.split("|")) for label in labels])

COMMON = "Chief complaint|History|Past history|Medications|Allergies|Vitals|Examination|Investigations|Assessment|Plan|Follow-up"
for key, title, kind in [("opd","OPD consultation","OPD"),("admission","Admission / H&P","Inpatient"),("progress","Daily progress note","Daily round"),("soap","SOAP note","Consultation"),("round","Ward round note","Ward round"),("consult","Consultation note","Consultation"),("followup","Follow-up note","Follow-up"),("referral","Referral note","Referral")]:
    add("General Medicine", "medicine_"+key,title,kind, COMMON if key!="soap" else "Subjective|Objective|Assessment|Plan|Allergies|Follow-up", "Allergies|Examination|Assessment|Plan|Follow-up")
DISCHARGE = "Admission diagnosis|Final diagnosis|Hospital course|Procedures|Investigations|Condition at discharge|Discharge medications|Instructions|Follow-up"
add("General Medicine","medicine_discharge","Discharge summary","Discharge",DISCHARGE,"Final diagnosis|Condition at discharge|Discharge medications|Follow-up","documents")
PREOP="Diagnosis|Indication|Planned procedure|Relevant history|Comorbidities|Allergies|Medications|Examination|Investigations|Imaging|Consent status"
OPERATIVE="Date and time|Surgeon|Assistants|Pre-op diagnosis|Post-op diagnosis|Procedure|Anesthesia|Findings|Technique|Specimens|Estimated blood loss|Complications|Drains and implants|Disposition"
add("Surgery","surgery_preop","Preoperative assessment","Pre-op",PREOP,"Indication|Planned procedure|Allergies|Consent status")
add("Surgery","surgery_operative","Operative report","Intra-op",OPERATIVE,"Procedure|Surgeon|Anesthesia|Findings|Estimated blood loss|Complications|Disposition")
add("Surgery","surgery_postop","Immediate postoperative note","Post-op","Patient status|Procedure completed|Findings|Estimated blood loss|Specimens|Drains|Complications|Immediate plan|Disposition","Patient status|Complications|Immediate plan|Disposition")
add("Surgery","surgery_progress","Postoperative progress","Ward / recovery","Subjective|Objective|Assessment|Plan|Allergies","Objective|Assessment|Plan")
add("Surgery","surgery_discharge","Surgical discharge summary","Discharge",DISCHARGE,"Final diagnosis|Condition at discharge|Follow-up","documents")
add("Surgery","surgery_followup","Surgical follow-up","Follow-up",COMMON+"|Wound assessment","Examination|Plan|Follow-up")
PED="Age|Weight|Height or length|Head circumference|Developmental stage|Birth history|Medications|Allergies|"
for key,title,kind,fields in [
 ("neonatal","Neonatal note","Neonatal","Gestational age|Delivery details|Feeding|Output|Examination|Neonatal concerns|Assessment|Plan"),
 ("acute","Pediatric acute visit","Acute visit","Chief complaint|HPI|Fever|Feeding|Vomiting or diarrhea|Respiratory symptoms|Urine and stool|Vitals|Examination|Assessment|Plan|Follow-up"),
 ("well","Well-child visit","Well-child","Growth|Development|Immunization|Nutrition|Screening|Examination|Anticipatory guidance|Plan|Follow-up"),
 ("admission","Pediatric admission","Inpatient",COMMON),
 ("progress","Pediatric progress","Daily round","Subjective|Objective|Assessment|Plan|Feeding|Output"),
 ("discharge","Pediatric discharge","Discharge",DISCHARGE)]:
    add("Pediatrics","pediatrics_"+key,title,kind,"|".join(dict.fromkeys((PED+fields).split("|"))),"Age|Weight|Allergies|Examination|Plan|Follow-up","documents" if key=="discharge" else "clinical")
PRENATAL="Gravida|Para|Pregnancy history|LMP|Gestational age|EDD|Previous obstetric history|Medical history|Surgical history|Medications|Allergies|Symptoms|Fetal movements|Bleeding|Pain|BP|Weight|Examination|Investigations|Ultrasound|Labs|Assessment|Plan|Follow-up"
for key,kind in [("prenatal","Prenatal"),("antepartum","Antepartum")]:
    add("OB/GYN","ob_"+key,kind+" note",kind,PRENATAL,"Gestational age|Allergies|BP|Assessment|Plan",stage="Obstetrics · "+kind)
add("OB/GYN","ob_labor","Labor & delivery progress","Labor","Labor progression|Cervical findings|Contractions|Fetal heart rate|Maternal vitals|Interventions|Medications|Delivery events|Mode of delivery|Complications|Plan","Maternal vitals|Fetal heart rate|Plan",stage="Obstetrics · Labor")
add("OB/GYN","ob_delivery","Delivery note","Delivery","Date and time|Mode|Indication|Baby details|Apgar|Placenta|Blood loss|Complications|Procedures|Disposition","Date and time|Mode|Baby details|Blood loss|Complications",stage="Obstetrics · Delivery")
add("OB/GYN","ob_postpartum","Postpartum note","Postpartum","Vitals|Pain|Bleeding or lochia|Uterine findings|Wound or incision|Breastfeeding|Urination and bowel|Mobility|Complications|Plan|Follow-up","Vitals|Bleeding or lochia|Plan",stage="Obstetrics · Postpartum")
add("OB/GYN","ob_discharge","Obstetric discharge","Discharge",DISCHARGE,"Condition at discharge|Follow-up","documents",stage="Obstetrics · Discharge")
add("OB/GYN","ob_followup","Obstetric follow-up","Follow-up",PRENATAL,"Assessment|Plan|Follow-up",stage="Obstetrics · Follow-up")
GYN="Complaint|Gynecological history|Menstrual and reproductive history|Medications|Allergies|Examination|Investigations|Imaging|Assessment|Plan|Follow-up"
for key,kind,fields in [("opd","OPD",GYN),("procedure","Procedure","Procedure|Indication|Consent status|Findings|Complications|Plan"),("preop","Pre-op",PREOP),("operative","Intra-op",OPERATIVE),("postop","Post-op","Patient status|Complications|Plan|Disposition"),("followup","Follow-up",GYN)]:
    add("OB/GYN","gyn_"+key,"Gynecology "+kind,kind,fields,"Allergies|Indication|Procedure|Examination|Estimated blood loss|Complications|Plan",stage="Gynecology · "+kind)
add("Radiology","radiology_report","Radiology report","Study / report","Exam|Modality|Body region|Contrast|Clinical indication|Technique|Comparison|Findings|Impression|Recommendation|Critical finding|Person contacted|Communication date and time|Communication method|Communication status","Exam|Clinical indication|Technique|Findings|Impression","reports")
CANCER="Cancer type|Primary site|Histology|Stage|Biomarkers|Metastatic sites|Previous treatment|Current treatment|Treatment intent|"
for key,title,kind,fields in [
 ("initial","Initial oncology consultation","Diagnosis","History|Timeline|Previous diagnosis|Investigations|Imaging|Pathology|Molecular testing|Assessment|Plan"),
 ("staging","Staging review","Staging","Investigations|Imaging|Pathology|Assessment|Plan"),
 ("planning","Treatment planning","Treatment planning","Regimen|Intent|Cycle|Drugs|Route|Schedule|Dose as dictated|Duration|Monitoring|Follow-up|Plan"),
 ("treatment","Treatment visit","Treatment","Current cycle|Symptoms|Toxicities|Investigations|Treatment response|Dose modification|Treatment delay|Plan|Next cycle or follow-up"),
 ("response","Response / toxicity review","Response / toxicity","Symptoms|Toxicities|Investigations|Treatment response|Assessment|Plan|Follow-up"),
 ("modification","Treatment modification","Treatment modification","Toxicities|Treatment response|Dose modification|Treatment delay|Clinician decision|Plan|Follow-up"),
 ("surveillance","Surveillance","Surveillance","Symptoms|Investigations|Imaging|Assessment|Plan|Follow-up"),
 ("survivorship","Survivorship / follow-up","Survivorship","Symptoms|Late effects|Surveillance plan|Assessment|Plan|Follow-up")]:
    add("Oncology","oncology_"+key,title,kind,CANCER+fields,"Cancer type|Stage|Treatment intent|Plan|Follow-up")

def undocumented(value):
    return not value.strip() or bool(re.search(r"\b(not documented|not mentioned|not discussed|not provided|not assessed|unknown)\b", value, re.I))

def checks(template, fields):
    missing=[dict(key=f["key"], message=f'{f["label"]} not documented') for f in template["fields"] if f["required"] and undocumented(fields.get(f["key"],{}).get("value", ""))]
    if template["specialty"]=="Radiology" and fields.get("critical_finding",{}).get("value", "").strip():
        missing += [dict(key=k,message=k.replace("_"," ").capitalize()+" not documented") for k in ["person_contacted","communication_date_and_time","communication_method","communication_status"] if undocumented(fields.get(k,{}).get("value", ""))]
    return missing

def suggest(department):
    text=(department or "").lower()
    for words,name in [(('radio',),'Radiology'),(('onco',),'Oncology'),(('pedia','neonat'),'Pediatrics'),(('gyn','obst','maternity'),'OB/GYN'),(('surg',),'Surgery')]:
        if any(word in text for word in words): return name
    return "General Medicine"


def suggest_template(department, transcript, age_days=None):
    specialty=suggest(department)
    # Suggestions only: explicit note-type phrases, never clinical decisions.
    phrases=[("operative report","surgery_operative"),("preoperative","surgery_preop"),("postoperative","surgery_postop"),("prenatal","ob_prenatal"),("antepartum","ob_antepartum"),("delivery note","ob_delivery"),("postpartum","ob_postpartum"),("gynecology","gyn_opd"),("ct abdomen","radiology_report"),("radiology report","radiology_report"),("oncology","oncology_treatment"),("well-child","pediatrics_well"),("neonatal","pediatrics_neonatal")]
    text=transcript.lower()
    for phrase,key in phrases:
        if phrase in text: return {"template_id":key,"specialty":TEMPLATES[key]["specialty"],"reason":f'Explicit dictation phrase: {phrase}. Confirm before use.'}
    candidates=[t for t in TEMPLATES.values() if t["specialty"]==specialty]
    if "discharge" in text:
        target=next((t for t in candidates if t["encounter_type"]=="Discharge"),None)
        if target:return {"template_id":target["id"],"specialty":specialty,"reason":"Department and dictated discharge workflow; confirm before use."}
    key=candidates[0]["id"]
    if specialty=="Pediatrics":key="pediatrics_neonatal" if age_days is not None and 0<=age_days<28 else "pediatrics_acute"
    return {"template_id":key,"specialty":specialty,"reason":"Suggested from the encounter department and patient context; confirm the encounter type."}
