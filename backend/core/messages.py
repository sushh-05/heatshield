import os
import re

def _field(value, name):
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name)

def _plan_parts(plan):
    hours = _field(plan, "hours")
    blocks = _field(plan, "blocks")
    return hours, blocks

def compose_message(plan, site_name, language="en"):
    hours, blocks = _plan_parts(plan)
    peak = max(hours, key=lambda item: _field(item, "forecast_heat_risk"))
    texts = {
        "en": ("HeatShield plan for {site}\nRisk: {level}\nPeak: {time} ({score}/100)\n\nHeavy work: follow the approved work blocks.\nRest: {rest} minutes each hour\nWater: every {water} minutes\n\nTap OK after reading. Tap I feel unwell if you need help.", "Forecast guidance only; follow your supervisor and site safety rules."),
        "hi": ("हीटशील्ड योजना: {site}\nजोखिम: {level}\nचरम समय: {time} ({score}/100)\n\nआराम: हर घंटे {rest} मिनट\nपानी: हर {water} मिनट\n\nपढ़ने के बाद OK दबाएं। अस्वस्थ महसूस हो तो मदद दबाएं।", "यह पूर्वानुमान आधारित मार्गदर्शन है; अपने सुपरवाइज़र और सुरक्षा नियमों का पालन करें।"),
        "te": ("హీట్‌షీల్డ్ ప్రణాళిక: {site}\nప్రమాదం: {level}\nగరిష్ఠ సమయం: {time} ({score}/100)\n\nవిరామం: ప్రతి గంటకు {rest} నిమిషాలు\nనీరు: ప్రతి {water} నిమిషాలకు\n\nచదివిన తర్వాత OK నొక్కండి. అస్వస్థంగా అనిపిస్తే సహాయం నొక్కండి.", "ఇది వాతావరణ అంచనా ఆధారిత మార్గదర్శకం; సూపర్‌వైజర్ మరియు భద్రతా నియమాలను పాటించండి."),
    }
    template, disclaimer = texts.get(language, texts["en"])
    control = blocks[hours.index(peak)]
    return template.format(site=site_name, level=_field(peak, "level"), time=_field(peak, "time"), score=_field(peak, "forecast_heat_risk"), rest=_field(control, "rest_minutes_per_hour"), water=_field(control, "water_every_minutes")) + "\n" + disclaimer

def safe_bedrock_message(plan, site_name, language="en"):
    """Optional wording only; reject any invented times, numbers, or medical claims."""
    canonical = compose_message(plan, site_name, language)
    if os.getenv("USE_BEDROCK", "false").lower() != "true": return canonical
    try:
        import boto3
        hours, blocks = _plan_parts(plan)
        allowed_times = {_field(hour, "time") for hour in hours}
        allowed_numbers = {str(_field(hour, "forecast_heat_risk")) for hour in hours} | {str(_field(block, "rest_minutes_per_hour")) for block in blocks} | {str(_field(block, "water_every_minutes")) for block in blocks}
        prompt = f"Simplify this message in {language}; do not change facts, times, or numbers:\n{canonical}"
        result = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "ap-south-1")).converse(modelId=os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0"), messages=[{"role":"user", "content":[{"text":prompt}]}], inferenceConfig={"maxTokens":180,"temperature":0.1})
        text = result["output"]["message"]["content"][0]["text"].strip()
        found_times = set(re.findall(r"\b\d{1,2}:\d{2}\b", text)); found_numbers = set(re.findall(r"\b\d+\b", text))
        if not text or len(text) > 1200 or not found_times.issubset(allowed_times) or not found_numbers.issubset(allowed_numbers | {"20", "100"}) or any(word in text.lower() for word in ("diagnose", "cure", "medicine")): return canonical
        return text + "\nForecast guidance only; follow your supervisor and site safety rules."
    except Exception:
        return canonical
