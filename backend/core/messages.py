def compose_message(plan, site_name, language="en"):
    peak = max(plan.hours, key=lambda item: item.forecast_heat_risk)
    texts = {
        "en": ("HeatShield plan for {site}\nRisk: {level}\nPeak: {time} ({score}/100)\n\nHeavy work: follow the approved work blocks.\nRest: {rest} minutes each hour\nWater: every {water} minutes\n\nTap OK after reading. Tap I feel unwell if you need help.", "Forecast guidance only; follow your supervisor and site safety rules."),
        "hi": ("हीटशील्ड योजना: {site}\nजोखिम: {level}\nचरम समय: {time} ({score}/100)\n\nआराम: हर घंटे {rest} मिनट\nपानी: हर {water} मिनट\n\nपढ़ने के बाद OK दबाएं। अस्वस्थ महसूस हो तो मदद दबाएं।", "यह पूर्वानुमान आधारित मार्गदर्शन है; अपने सुपरवाइज़र और सुरक्षा नियमों का पालन करें।"),
        "te": ("హీట్‌షీల్డ్ ప్రణాళిక: {site}\nప్రమాదం: {level}\nగరిష్ఠ సమయం: {time} ({score}/100)\n\nవిరామం: ప్రతి గంటకు {rest} నిమిషాలు\nనీరు: ప్రతి {water} నిమిషాలకు\n\nచదివిన తర్వాత OK నొక్కండి. అస్వస్థంగా అనిపిస్తే సహాయం నొక్కండి.", "ఇది వాతావరణ అంచనా ఆధారిత మార్గదర్శకం; సూపర్‌వైజర్ మరియు భద్రతా నియమాలను పాటించండి."),
    }
    template, disclaimer = texts.get(language, texts["en"])
    control = plan.blocks[plan.hours.index(peak)]
    return template.format(site=site_name, level=peak.level, time=peak.time, score=peak.forecast_heat_risk, rest=control.rest_minutes_per_hour, water=control.water_every_minutes) + "\n" + disclaimer
