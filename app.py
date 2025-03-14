from flask import Flask, request, render_template, jsonify, session
import requests
from geopy.distance import geodesic
from google import genai

app = Flask(__name__)
app.secret_key = "healthcare_gemini_app_secret_key"  # 세션을 사용하기 위한 시크릿 키

# Gemini API setup
genai_client = genai.Client(api_key="AIzaSyBq-hPsmp9GxmF08z0Fnkh6l53SM3sQq08")

# Existing functions for location services
def get_lat_lng(street, city, state, country):
    full_address = f"{street}, {city}, {state}, {country}"
    url = f"https://nominatim.openstreetmap.org/search?q={full_address}&format=json"

    headers = {
        "User-Agent": "MyHealthcareApp/1.0 (your@email.com)"
    }

    response = requests.get(url, headers=headers)
    
    print("🔹 Requested Address:", full_address)
    print("🔹 API Response Code:", response.status_code)
    print("🔹 API Response Text:", response.text)

    if response.status_code == 403:
        print("❌ Error: Blocked by Nominatim. Try again later.")
        return None, None

    try:
        data = response.json()
        if not data:
            print("❌ No results found for this address.")
            return None, None
    except requests.exceptions.JSONDecodeError:
        print("❌ Error: Response is not in JSON format!")
        return None, None

    print("✅ Parsed Lat:", data[0]["lat"], "Lng:", data[0]["lon"])
    return float(data[0]["lat"]), float(data[0]["lon"])

def get_nearby_healthcare(lat, lng, radius=5000):
    overpass_url = "https://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json];
    node
      (around:{radius},{lat},{lng})
      ["amenity"~"hospital|clinic|pharmacy"];
    out;
    """
    response = requests.get(overpass_url, params={"data": overpass_query}).json()

    places = []
    for node in response.get("elements", []):
        place = {
            "name": node.get("tags", {}).get("name", "Unknown"),
            "lat": node["lat"],
            "lng": node["lon"],
            "address": node.get("tags", {}).get("addr:street", "Unknown Address"),
        }
        places.append(place)

    return places

def get_211_resources(lat, lng):
    url = f"https://api.211.org/resources?latitude={lat}&longitude={lng}&radius=5000"
    response = requests.get(url)
    
    resources = []
    if response.status_code == 200:
        data = response.json()
        for resource in data.get('data', []):
            resource_details = {
                "name": resource.get("name", "Unknown Resource"),
                "lat": lat,
                "lng": lng,
                "address": resource.get("address", "Unknown Address")
            }
            resources.append(resource_details)
    else:
        print(f"❌ Error fetching 211 resources: {response.status_code}")
    
    return resources

def sort_by_distance(user_lat, user_lng, places):
    user_location = (user_lat, user_lng)
    return sorted(
        places, 
        key=lambda place: geodesic(user_location, (place["lat"], place["lng"])).miles
    )

# New function for Gemini AI with healthcare restriction
def get_gemini_assistance(query):
    try:
        # Add system prompt to enforce healthcare-related responses only
        system_prompt = (
            "You are a strict healthcare assistant. You ONLY provide information related to healthcare, "
            "medicine, medical conditions, treatments, wellness, and similar topics. "
            "If the user asks anything outside of healthcare, refuse to answer."
        )

        full_prompt = f"{system_prompt}\n\nUser Query: {query}"
        
        response = genai_client.models.generate_content(
            model="gemini-2.0-flash", 
            contents=full_prompt
        )
        return response.text
    except Exception as e:
        print(f"Gemini API error: {e}")
        return f"Sorry, I couldn't process your request: {str(e)}"

# 위치 추천 전용 함수 추가
def get_location_recommendation(query):
    try:
        system_prompt = (
            "You are a healthcare location advisor. Focus ONLY on recommending appropriate "
            "healthcare facilities based on the user's needs. Do not repeat health information or advice. "
            "Be direct and concise about which facility to visit and why."
        )

        full_prompt = f"{system_prompt}\n\nRequest: {query}"
        
        response = genai_client.models.generate_content(
            model="gemini-2.0-flash", 
            contents=full_prompt
        )
        return "RECOMMENDATION: " + response.text
    except Exception as e:
        print(f"Gemini API error: {e}")
        return f"RECOMMENDATION: Sorry, I couldn't process your request: {str(e)}"

@app.route("/", methods=["GET", "POST"])
def index():
    # GET 요청일 경우 세션 초기화 (새로운 페이지 접속)
    if request.method == "GET":
        session.pop('ai_response', None)
        session.pop('ai_query', None)
        session.pop('location_recommendation', None)
    
    # 세션에 ai_response가 없으면 초기화
    if 'ai_response' not in session:
        session['ai_response'] = None
    
    # 위치 기반 추천을 위한 세션 변수 추가
    if 'location_recommendation' not in session:
        session['location_recommendation'] = None
    
    places = None
    address = None
    error = None
    
    if request.method == "POST":
        if 'search' in request.form:
            # Process address search
            street = request.form["street"]
            city = request.form["city"]
            state = request.form["state"]
            country = request.form["country"]

            lat, lng = get_lat_lng(street, city, state, country)
            if lat is None or lng is None:
                error = "Invalid address. Please try again."
            else:
                # Fetch healthcare locations and 211 resources
                healthcare_places = get_nearby_healthcare(lat, lng)
                resources_211 = get_211_resources(lat, lng)
                
                # Combine both sources of resources
                all_places = healthcare_places + resources_211
                
                # Sort all places by distance
                places = sort_by_distance(lat, lng, all_places)[:5]  # Top 5 results
                address = f"{street}, {city}, {state}, {country}"
                
                # If there's an AI query, generate location recommendations
                if 'ai_query' in session and session['ai_query'] and session['ai_response']:
                    location_info = f"Available healthcare resources near {address}: "
                    for place in places[:3]:  # First 3 resources
                        location_info += place['name'] + ", "
                    
                    # 별도의 함수를 사용하여 위치 추천만 받아오기
                    recommendation_text = f"""
                    Based on health query: {session['ai_query']}
                    Available facilities: {location_info}
                    Recommend the most appropriate facility and explain why in 1-2 sentences.
                    """
                    
                    # 위치 추천 전용 함수 사용
                    session['location_recommendation'] = get_location_recommendation(recommendation_text)
        
        elif 'ask_ai' in request.form:
            # Process AI query
            query = request.form.get('ai_query', '')
            if query:
                session['ai_query'] = query
                session['ai_response'] = get_gemini_assistance(query)
                # 위치 추천 초기화
                session['location_recommendation'] = None
            else:
                error = "Please enter a question for the AI assistant."

    # 템플릿에 두 응답을 별도로 전달
    return render_template(
        "index.html", 
        places=places, 
        address=address, 
        error=error, 
        ai_response=session.get('ai_response'),
        location_recommendation=session.get('location_recommendation')
    )

if __name__ == "__main__":
    app.run(debug=True)