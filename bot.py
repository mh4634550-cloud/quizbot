async def generate_ai_text(prompt, image_bytes=None):
    if API_KEY.startswith("gsk_"):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        if image_bytes:
            b64_img = base64.b64encode(image_bytes).decode('utf-8')
            payload = {
                "model": "llama-3.2-11b-vision-preview",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                        ]
                    }
                ],
                "temperature": 0.3
            }
        else:
            payload = {
                "model": "llama3-70b-8192",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4
            }

        async with ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data["choices"][0]["message"]["content"]
                else:
                    raise Exception(f"Groq API Error: {str(data)[:100]}")
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={API_KEY}"
        parts = []
        if image_bytes:
            b64_data = base64.b64encode(image_bytes).decode('utf-8')
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64_data}})
        parts.append({"text": prompt})
        
        async with ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": parts}]}, headers={"Content-Type": "application/json"}) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                else:
                    raise Exception(f"Gemini API Error: {str(data)[:100]}")
