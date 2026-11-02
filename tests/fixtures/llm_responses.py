"""Canned 'ideal' LLM responses for the 10 dataset emails, keyed by email subject.

These represent what a correct extraction / classification / evidence match looks like. They let
the whole workflow be tested deterministically without network access.
"""

from __future__ import annotations

EMPTY = dict(customer_name=None, policy_number=None, event_date=None, event_location=None, event_description=None,
             loss_damage_details=None, claimed_amount_text=None, supporting_documents=[], pending_documents=[],
             customer_request="Help filing a claim", intent="new_claim", vehicle_registration=None, fir_number=None,
             hospital_name=None, admission_date=None, discharge_date=None, insured_person_name=None,
             claimant_relationship=None, ambiguities=[])


def extraction(**fields) -> dict:
    return {**EMPTY, **fields}


CASES: dict[str, dict] = {
    "Water damage in my bathroom": dict(
        email_id="EMAIL-01", type="Home Insurance",
        extraction=extraction(customer_name="Neha Kapoor", policy_number="HOME-2026-1001", event_date="2026-10-25",
                              event_location="Mumbai", event_description="Geyser burst in the bathroom causing water damage.",
                              loss_damage_details="Bathroom tiles and wooden cabinet damaged.",
                              claimed_amount_text="Rs. 45,000",
                              supporting_documents=["photographs of the damage", "plumber's estimate"]),
        match=dict(matched_coverages=["Accidental water leakage from burst pipes"],
                   coverage_evidence="my geyser burst in the bathroom", exclusion_findings=[], reason="Burst geyser."),
        expected=("APPROVED", "40000.00")),
    "Burglary at my home": dict(
        email_id="EMAIL-02", type="Home Insurance",
        extraction=extraction(customer_name="Sanjay Mehta", policy_number="HOME-2026-1002", event_date="2026-10-27",
                              event_location="Pune", event_description="Home broken into through a window.",
                              loss_damage_details="Television and laptop stolen, window glass broken.",
                              claimed_amount_text="Rs. 1,20,000",
                              supporting_documents=["copy of the police FIR", "photographs of the broken window"]),
        match=dict(matched_coverages=["Theft involving forced entry"],
                   coverage_evidence="my home in Pune was broken into through a window", exclusion_findings=[],
                   reason="Forced entry theft."),
        expected=("PENDING", None)),
    "Fire damage in my living room": dict(
        email_id="EMAIL-03", type="Home Insurance",
        extraction=extraction(customer_name="Deepa Iyer", event_date="2026-10-26", event_location="Bangalore",
                              event_description="Electrical short circuit caused a fire in the living room.",
                              loss_damage_details="Sofa and curtains burnt.", claimed_amount_text="Rs. 35,000",
                              supporting_documents=["photographs of the damaged items"]),
        match=None, expected=("PENDING", None)),
    "Engine breakdown claim": dict(
        email_id="EMAIL-04", type="Vehicle Insurance",
        extraction=extraction(customer_name="Karan Mehta", policy_number="VEH-2026-2001", event_date="2026-10-24",
                              event_location="highway", event_description="Car engine stopped working on the highway.",
                              loss_damage_details="Engine mechanical breakdown due to wear and tear.",
                              claimed_amount_text="Rs. 95,000",
                              supporting_documents=["mechanic's inspection report", "repair estimate"]),
        match=dict(matched_coverages=[], coverage_evidence=None, reason="Mechanical failure.",
                   exclusion_findings=[
                       dict(exclusion="Mechanical breakdown",
                            evidence="the engine had suffered a mechanical breakdown due to wear and tear from long-term use"),
                       dict(exclusion="Normal wear and tear", evidence="wear and tear from long-term use")]),
        expected=("REJECTED", None)),
    "My car has been stolen": dict(
        email_id="EMAIL-05", type="Vehicle Insurance",
        extraction=extraction(customer_name="Arjun Rao", policy_number="VEH-2026-2002", event_date="2026-10-26",
                              event_location="Bangalore", event_description="Car stolen from apartment parking area.",
                              loss_damage_details="Hyundai Creta stolen.", claimed_amount_text="Rs. 6,50,000",
                              vehicle_registration="KA 05 CD 5678", fir_number="BLR/2026/1026/458",
                              supporting_documents=["copy of the FIR", "vehicle registration certificate"]),
        match=dict(matched_coverages=["Vehicle theft"], coverage_evidence="My car was stolen from my apartment parking area",
                   exclusion_findings=[], reason="Theft."),
        expected=("APPROVED", "648000.00")),
    "Cracked windshield due to falling tree branch": dict(
        email_id="EMAIL-06", type="Vehicle Insurance",
        extraction=extraction(customer_name="Sneha Kulkarni", policy_number="VEH-2026-2003", event_date="2026-10-27",
                              event_description="Tree branch fell on the car during heavy rain.",
                              loss_damage_details="Windshield cracked.", vehicle_registration="KA 01 EF 9012",
                              supporting_documents=["photographs of the damaged windshield"],
                              pending_documents=["garage's repair quote"]),
        match=None, expected=("PENDING", None)),
    "Request for hospitalisation claim": dict(
        email_id="EMAIL-07", type="Medical Insurance",
        extraction=extraction(customer_name="Meera Joshi", policy_number="MED-2026-3001", event_date="2026-10-20",
                              event_location="Apollo Hospital, Bangalore",
                              event_description="Admitted for appendicitis and underwent surgery.",
                              loss_damage_details="Surgery and four-day hospital stay.",
                              claimed_amount_text="Rs. 1,20,000", hospital_name="Apollo Hospital",
                              admission_date="2026-10-20", discharge_date="2026-10-24",
                              supporting_documents=["hospital bills", "discharge summary", "medical reports"]),
        match=dict(matched_coverages=["Hospitalisation", "Surgery"], coverage_evidence="underwent surgery",
                   exclusion_findings=[], reason="Hospitalisation and surgery."),
        expected=("APPROVED", "120000.00")),
    "Hospitalisation reimbursement request": dict(
        email_id="EMAIL-08", type="Medical Insurance",
        extraction=extraction(customer_name="Vikram Shetty", policy_number="MED-2026-3002", event_date="2026-10-22",
                              event_description="Admitted to Manipal Hospital with dengue.",
                              loss_damage_details="Four days of inpatient treatment.", claimed_amount_text="Rs. 48,000",
                              hospital_name="Manipal Hospital", admission_date="2026-10-22",
                              discharge_date="2026-10-26",
                              supporting_documents=["hospital bill", "discharge summary"]),
        match=dict(matched_coverages=["Hospitalisation"],
                   coverage_evidence="I was admitted to Manipal Hospital on 22 October 2026",
                   exclusion_findings=[], reason="Hospitalisation."),
        expected=("PENDING", None)),
    "Request to initiate death claim": dict(
        email_id="EMAIL-09", type="Life Insurance",
        extraction=extraction(customer_name="Lakshmi Kumar", policy_number="LIFE-2026-4001", event_date="2026-10-15",
                              event_description="Insured passed away due to natural illness.",
                              loss_damage_details="Death of the insured, Ramesh Kumar.",
                              claimed_amount_text="Rs. 15,00,000", insured_person_name="Ramesh Kumar",
                              claimant_relationship="wife and registered nominee",
                              supporting_documents=["death certificate", "required supporting documents"]),
        match=dict(matched_coverages=["Natural death"],
                   coverage_evidence="He passed away on 15 October 2026 due to a natural illness",
                   exclusion_findings=[], reason="Natural death."),
        expected=("APPROVED", "1500000.00")),
    "Need assistance with a life insurance claim": dict(
        email_id="EMAIL-10", type="Life Insurance",
        extraction=extraction(customer_name="Amit Mehta", event_date="2026-10-22",
                              event_description="Brother passed away following a road accident.",
                              loss_damage_details="Death of the insured, Rohan Mehta.",
                              insured_person_name="Rohan Mehta", claimant_relationship="brother, registered nominee",
                              fir_number="BLR/2026/1022/789", pending_documents=["death certificate"],
                              customer_request="How to initiate the claim", intent="enquiry"),
        match=None, expected=("PENDING", None)),
}
