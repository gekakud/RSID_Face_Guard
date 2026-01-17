import requests
import json

#MAC = "2c:cf:67:f5:ce:ca"   #
MAC = "111"
url = "https://geine-server.onrender.com/getAllTicketDeviceAccessByMacAdress"
payload = {"mac": MAC}

print("Calling URL:", url)
print("Payload:", payload)

try:
    response = requests.post(url, json=payload)
    print("Status code:", response.status_code)
    print("Raw response text:\n", response.text)

    # Raise if not 2xx
    response.raise_for_status()

    data = response.json()
    print("Parsed JSON:")
    print(json.dumps(data, indent=2))
    print("Raw length:", len(response.text))
    print("Raw repr:  ", repr(response.text))


except requests.exceptions.HTTPError as e:
    print("HTTP error:", e)
except requests.exceptions.RequestException as e:
    print("Request error:", e)




