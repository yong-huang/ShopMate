"""The 30 hand-curated catalog records (ticket T02).

Each record is a rich, bilingual product: detailed specs, 3-5 reviews, and
hand-written English/Chinese copy. These are the storefront's featured
products and give the demo (and the eval golden set) deep content for
product introduction, comparison, and review-grounded answers.

Shape notes:
- ``specs`` is an ordered list of ``{"label", "value"}`` for side-by-side
  comparison rendering.
- ``reviews`` entries are ``{"author", "rating", "comment"}``; ratings are
  integers 1-5 and the product ``rating`` is the rounded mean.
- Apparel records additionally carry ``sizing`` / ``care`` / ``materials``
  (System Design 3.8); other fields may be English-only per PRD FR-7.
"""

from typing import Any


def record(id_: str, slug: str, name_en: str, name_zh: str, price: float,
           description_en: str, description_zh: str, attributes: dict[str, Any],
           specs: list[dict[str, str]], reviews: list[dict[str, Any]],
           tags: list[str], stock: int, apparel: dict[str, Any] | None = None
           ) -> dict[str, Any]:
    """Build one curated product record with the derived rating field."""
    product: dict[str, Any] = {
        "id": id_,
        "slug": slug,
        "name_en": name_en,
        "name_zh": name_zh,
        "description_en": description_en,
        "description_zh": description_zh,
        "category": _CATEGORY_BY_PREFIX[id_.split("-")[0]],
        "price": price,
        "currency": "USD",
        "attributes": attributes,
        "specs": specs,
        "reviews": reviews,
        "rating": round(sum(r["rating"] for r in reviews) / len(reviews), 1),
        "stock": stock,
        "tags": tags,
        "featured": True,
        "image": f"images/{id_}.svg",
    }
    if apparel is not None:
        product.update(apparel)
    return product


_CATEGORY_BY_PREFIX = {
    "ELE": "Electronics",
    "HOM": "Home & Kitchen",
    "APP": "Apparel & Accessories",
    "SPO": "Sports & Outdoors",
}

# 8 Electronics + 8 Home & Kitchen + 7 Apparel + 7 Sports = 30 curated.
CURATED_PRODUCTS: list[dict[str, Any]] = [
    record(
        "ELE-001", "aurora-x9-wireless-noise-cancelling-headphones",
        "Aurora X9 Wireless Noise-Cancelling Headphones",
        "极光 X9 无线降噪耳机",
        249.99,
        "The Aurora X9 pairs 40 mm graphene drivers with hybrid active noise "
        "cancellation that adapts to your surroundings 200 times per second. "
        "A 45-hour battery, multipoint Bluetooth 5.3 and a featherlight 245 g "
        "frame make it the everyday companion for commutes and deep work.",
        "极光 X9 采用 40 毫米石墨烯单元，混合主动降噪每秒 200 次自适应环境变化。"
        "45 小时续航、蓝牙 5.3 双设备连接与仅 245 克的轻量机身，"
        "是通勤与专注工作的日常搭档。",
        {"brand": "Aurora", "model": "X9", "colour": "Midnight Black",
         "warranty": "18 months"},
        [{"label": "Drivers", "value": "40 mm graphene"},
         {"label": "Noise cancellation", "value": "Hybrid ANC, adaptive"},
         {"label": "Battery life", "value": "45 h (ANC on)"},
         {"label": "Bluetooth", "value": "5.3, multipoint"},
         {"label": "Weight", "value": "245 g"},
         {"label": "Charging", "value": "USB-C, 10 min = 5 h"}],
        [{"author": "Priya", "rating": 5,
          "comment": "Ancient-plane roar simply disappears. Best commute "
                     "upgrade I have bought in years."},
         {"author": "Marcus", "rating": 5,
          "comment": "Multipoint switching between laptop and phone is "
                     "flawless, and the battery easily lasts my week."},
         {"author": "Elena", "rating": 4,
          "comment": "Superb sound and comfort; the touch controls took a "
                     "day to get used to."}],
        ["headphones", "noise cancelling", "wireless", "bluetooth", "travel"],
        120),
    record(
        "ELE-002", "nimbus-15-pro-laptop",
        "Nimbus 15 Pro Laptop (16 GB / 512 GB)",
        "云帆 15 Pro 笔记本（16GB / 512GB）",
        1099.00,
        "A 15.6-inch 3K OLED display and a 12-core processor wrapped in a "
        "1.35 kg aluminium chassis. The Nimbus 15 Pro stays whisper-quiet "
        "under load and still delivers a full workday of 11-hour battery.",
        "云帆 15 Pro 配备 15.6 英寸 3K OLED 屏与 12 核处理器，铝合金机身仅 1.35 公斤。"
        "高负载下依旧安静冷静，11 小时电池足以支撑整个工作日。",
        {"brand": "Nimbus", "model": "15 Pro", "colour": "Space Grey",
         "warranty": "24 months"},
        [{"label": "Display", "value": "15.6\" 3K OLED, 120 Hz"},
         {"label": "Processor", "value": "12-core, 4.7 GHz boost"},
         {"label": "Memory", "value": "16 GB LPDDR5"},
         {"label": "Storage", "value": "512 GB NVMe SSD"},
         {"label": "Battery", "value": "11 h typical use"},
         {"label": "Weight", "value": "1.35 kg"}],
        [{"author": "Tom", "rating": 5,
          "comment": "The OLED screen is stunning for photo editing and the "
                     "fan is basically inaudible."},
         {"author": "Sofia", "rating": 4,
          "comment": "Fast, light and well built. I wish it had one more "
                     "USB-A port."},
         {"author": "Daniel", "rating": 5,
          "comment": "Compiles our codebase noticeably faster than my old "
                     "machine and survives a full day of meetings."}],
        ["laptop", "oled", "ultrabook", "work", "portable"],
        45),
    record(
        "ELE-003", "pulsefit-5-smartwatch",
        "PulseFit 5 Smartwatch",
        "脉动 Fit 5 智能手表",
        179.00,
        "PulseFit 5 tracks 120+ sport modes, blood oxygen and sleep stages on "
        "an always-on AMOLED display. Dual-band GPS locks your running route "
        "in seconds and the battery stretches to 14 days between charges.",
        "脉动 Fit 5 支持 120 余种运动模式，常亮 AMOLED 屏上呈现血氧与睡眠分期。"
        "双频 GPS 数秒锁定跑步轨迹，续航长达 14 天。",
        {"brand": "PulseFit", "model": "PF5", "colour": "Graphite",
         "warranty": "12 months"},
        [{"label": "Display", "value": "1.43\" AMOLED, always-on"},
         {"label": "GPS", "value": "Dual-band, 5 satellite systems"},
         {"label": "Battery", "value": "14 days typical, 30 h GPS"},
         {"label": "Water resistance", "value": "5 ATM"},
         {"label": "Sensors", "value": "HR, SpO2, sleep, stress"}],
        [{"author": "Ava", "rating": 5,
          "comment": "Route tracking is spot on even under dense trees, and "
                     "two weeks of battery is liberating."},
         {"author": "Ken", "rating": 4,
          "comment": "Accurate heart rate for intervals; third-party watch "
                     "faces would be nice."},
         {"author": "Lucia", "rating": 5,
          "comment": "Sleep staging matches how I actually feel in the "
                     "morning. The vibration alarm is gentle but effective."}],
        ["smartwatch", "fitness", "gps", "health", "wearable"],
        200),
    record(
        "ELE-004", "echobeam-bluetooth-speaker",
        "EchoBeam Bluetooth Speaker",
        "回声光束蓝牙音箱",
        89.99,
        "A 30 W stereo driver set with passive radiators that fills a room — "
        "or a campsite — without distortion. IP67 waterproofing and 20-hour "
        "playback make the EchoBeam as happy outdoors as on a desk.",
        "30 瓦立体声单元与无源辐射器，无论房间还是营地都饱满无损。"
        "IP67 防水与 20 小时播放，室内户外同样从容。",
        {"brand": "EchoBeam", "model": "EB-2", "colour": "Forest Green",
         "warranty": "12 months"},
        [{"label": "Output", "value": "30 W stereo + radiators"},
         {"label": "Battery", "value": "20 h playback"},
         {"label": "Waterproof", "value": "IP67"},
         {"label": "Bluetooth", "value": "5.3, 30 m range"},
         {"label": "Weight", "value": "680 g"}],
        [{"author": "Ravi", "rating": 5,
          "comment": "Took it rafting — dunked it twice, kept playing. "
                     "Sound is far bigger than the box."},
         {"author": "Mia", "rating": 4,
          "comment": "Great mids and vocals; at max volume the bass thins "
                     "out a little."},
         {"author": "Jonas", "rating": 5,
          "comment": "Pairing two of them in stereo mode on the patio was "
                     "effortless."}],
        ["speaker", "bluetooth", "waterproof", "outdoor", "music"],
        180),
    record(
        "ELE-005", "lumiview-4k-projector",
        "LumiView 4K Projector",
        "流明视 4K 投影仪",
        549.00,
        "A native 4K picture at 2200 ISO lumens with automatic keystone and "
        "focus, so movie night starts in seconds. The LumiView throws a "
        "usable 100-inch image from just 2.5 metres and runs streaming apps "
        "natively.",
        "流明视以 2200 ISO 流明呈现原生 4K 画面，自动梯形校正与对焦，"
        "数秒开启家庭影院。2.5 米即可投出 100 英寸画面，内置流媒体应用。",
        {"brand": "LumiView", "model": "LV4K", "colour": "White",
         "warranty": "24 months"},
        [{"label": "Resolution", "value": "Native 4K UHD"},
         {"label": "Brightness", "value": "2200 ISO lumens"},
         {"label": "Throw ratio", "value": "0.25:1 short throw"},
         {"label": "Correction", "value": "Auto keystone + auto focus"},
         {"label": "Smart OS", "value": "Built-in streaming apps"},
         {"label": "Noise", "value": "< 28 dB fan"}],
        [{"author": "Grace", "rating": 5,
          "comment": "Replaced our TV entirely. Sharp text, vivid colour, "
                     "and setup took five minutes."},
         {"author": "Victor", "rating": 4,
          "comment": "Bright enough with curtains open; built-in apps cover "
                     "everything we watch."},
         {"author": "Hana", "rating": 5,
          "comment": "The auto focus is magic for kids' movie nights — they "
                     "move it, it fixes itself."}],
        ["projector", "4k", "home cinema", "streaming"],
        60),
    record(
        "ELE-006", "vertex-1080p-webcam",
        "Vertex 1080p Webcam",
        "顶点 1080p 网络摄像头",
        69.99,
        "A Sony sensor with HDR keeps you well-lit against windows, and "
        "on-device framing keeps you centred without a subscription. The "
        "privacy shutter and mute indicator are physical, not software.",
        "索尼传感器配合 HDR，逆光依旧清晰；端侧取景始终把你保持在画面中央，无需订阅。"
        "隐私快门与静音指示均为物理设计。",
        {"brand": "Vertex", "model": "VX1080", "colour": "Slate",
         "warranty": "12 months"},
        [{"label": "Resolution", "value": "1080p @ 60 fps"},
         {"label": "Sensor", "value": '1/2.8" Sony, HDR'},
         {"label": "Field of view", "value": "78°, auto framing"},
         {"label": "Microphone", "value": "Dual, noise reducing"},
         {"label": "Mount", "value": "Clip + 1/4\" tripod thread"}],
        [{"author": "Omar", "rating": 5,
          "comment": "Colours finally look right on video calls, even with "
                     "a bright window behind me."},
         {"author": "Becky", "rating": 4,
          "comment": "Auto framing is smooth. Would love 4K, but at this "
                     "price it is a steal."},
         {"author": "Chris", "rating": 5,
          "comment": "Physical privacy shutter was the deciding factor — "
                     "and the image quality is excellent anyway."}],
        ["webcam", "video calls", "1080p", "wfh"],
        300),
    record(
        "ELE-007", "swiftcharge-100w-gan-charger",
        "SwiftCharge 100W GaN Charger",
        "疾充 100W 氮化镓充电器",
        59.99,
        "Four ports, 100 W total, and a pocketable GaN body that stays cool "
        "even while charging a laptop and two phones at once. Intelligent "
        "power splitting means nothing fights for watts.",
        "四口合计 100W，氮化镓机身小巧且低温，可同时为笔记本与两部手机快充。"
        "智能功率分配，多设备互不争抢。",
        {"brand": "SwiftCharge", "model": "SC100", "colour": "White",
         "warranty": "18 months"},
        [{"label": "Total output", "value": "100 W (single port)"},
         {"label": "Ports", "value": "3x USB-C + 1x USB-A"},
         {"label": "Technology", "value": "GaN III"},
         {"label": "Size", "value": "66 x 56 x 30 mm"},
         {"label": "Safety", "value": "Over-current/temp protection"}],
        [{"author": "Lena", "rating": 5,
          "comment": "One brick now travels with me for laptop, tablet and "
                     "phone. Barely warm under full load."},
         {"author": "Peter", "rating": 5,
          "comment": "Charges my 16-inch laptop at full speed and still has "
                     "ports to spare."},
         {"author": "Yuki", "rating": 4,
          "comment": "Compact and reliable; the folded prongs would make it "
                     "perfect."}],
        ["charger", "gan", "usb-c", "travel", "fast charging"],
        400),
    record(
        "ELE-008", "skydrone-mini-4k-camera-drone",
        "SkyDrone Mini 4K Camera Drone",
        "云雀迷你 4K 航拍无人机",
        399.00,
        "Under 249 g so it dodges registration in most regions, yet it films "
        "stabilised 4K/30 with a 3-axis gimbal. 31 minutes of flight, "
        "automatic return-to-home and subject tracking come standard.",
        "重量不足 249 克，多数地区免注册；三轴云台稳定输出 4K/30 视频。"
        "31 分钟续航，自动返航与智能跟拍皆为标配。",
        {"brand": "SkyDrone", "model": "Mini4K", "colour": "Grey",
         "warranty": "12 months"},
        [{"label": "Camera", "value": "4K/30, 3-axis gimbal"},
         {"label": "Weight", "value": "241 g"},
         {"label": "Flight time", "value": "31 min"},
         {"label": "Range", "value": "10 km video link"},
         {"label": "Modes", "value": "Track, cruise, quick shots"}],
        [{"author": "Andres", "rating": 5,
          "comment": "Footage looks gimbal-smooth in coastal wind. It is "
                     "genuinely pocketable."},
         {"author": "Nora", "rating": 4,
          "comment": "Beginner-friendly with return-to-home. Wind above 30 "
                     "km/h is its limit."},
         {"author": "Felix", "rating": 5,
          "comment": "Subject tracking on my bike rides is reliable and "
                     "the battery claims are honest."}],
        ["drone", "camera", "4k", "aerial", "travel"],
        55),

    record(
        "HOM-001", "brewmaster-pro-espresso-machine",
        "BrewMaster Pro Espresso Machine",
        "醇萃大师 Pro 意式咖啡机",
        429.00,
        "A 58 mm commercial group head and PID-controlled thermoblock pull "
        "cafe-grade shots, while the steam wand turns milk into microfoam in "
        "20 seconds. Built-in burr grinder with 30 settings closes the loop "
        "from bean to cup.",
        "58 毫米商用冲煮头与 PID 控温即热锅炉萃取专业级浓缩，蒸汽棒 20 秒打出绵密奶泡。"
        "内置 30 档锥刀研磨，从咖啡豆到一杯浓缩一气呵成。",
        {"brand": "BrewMaster", "model": "BM-Pro", "colour": "Stainless",
         "warranty": "24 months"},
        [{"label": "Group head", "value": "58 mm commercial"},
         {"label": "Heating", "value": "PID thermoblock, 3 s heat-up"},
         {"label": "Grinder", "value": "Conical burr, 30 settings"},
         {"label": "Pressure", "value": "9 bar regulated"},
         {"label": "Steam wand", "value": "Professional microfoam"},
         {"label": "Water tank", "value": "2 L removable"}],
        [{"author": "Beatrice", "rating": 5,
          "comment": "Taste is a genuine step above my cafe habit — and "
                     "cheaper by the third week."},
         {"author": "Hugo", "rating": 4,
          "comment": "Excellent espresso; the learning curve for dialing in "
                     "grind took a few days."},
         {"author": "Sandra", "rating": 5,
          "comment": "Latte art quality microfoam at home. Cleanup is "
                     "easier than my old machine."}],
        ["espresso", "coffee", "grinder", "kitchen", "breakfast"],
        70),
    record(
        "HOM-002", "everheat-smart-kettle",
        "EverHeat Smart Kettle",
        "恒热智能电热水壶",
        79.99,
        "Six precise temperature presets keep green tea at 80 °C and coffee "
        "at 93 °C, and hold them warm for an hour. The gooseneck spout "
        "pours slow and steady for pour-over beginners and obsessives alike.",
        "六档精准控温：绿茶 80°C、咖啡 93°C，并可保温一小时。鹅颈壶嘴出水缓慢稳定，"
        "无论手冲新手还是老饕都得心应手。",
        {"brand": "EverHeat", "model": "EH-1", "colour": "Matte Black",
         "warranty": "12 months"},
        [{"label": "Capacity", "value": "1.0 L"},
         {"label": "Presets", "value": "6 temperatures, 40-100 °C"},
         {"label": "Keep warm", "value": "60 min hold"},
         {"label": "Spout", "value": "Precision gooseneck"},
         {"label": "Body", "value": "Double-wall stainless"}],
        [{"author": "Ines", "rating": 5,
          "comment": "The 80 °C preset transformed my green tea. Pour-over "
                     "control is superb."},
         {"author": "Martin", "rating": 4,
          "comment": "Heats fast and the lid seals well. Base could show "
                     "the current temp more brightly."},
         {"author": "Chloe", "rating": 5,
          "comment": "Quiet, stable pouring and the keep-warm is perfect "
                     "for slow Sunday mornings."}],
        ["kettle", "tea", "pour over", "temperature control", "kitchen"],
        250),
    record(
        "HOM-003", "crispair-5-5l-air-fryer",
        "CrispAir 5.5L Air Fryer",
        "脆风 5.5 升空气炸锅",
        129.99,
        "A 5.5-litre square basket fits a whole chicken, and dual-zone "
        "radiant heat crisps food with up to 90 percent less oil. Twelve "
        "one-touch presets cover fries to salmon, and every removable part "
        "is dishwasher safe.",
        "5.5 升方形炸篮可容纳整只鸡，双区辐射加热令食物酥脆，用油量减少高达 90%。"
        "12 项一键预设从薯条到三文鱼，可拆部件均可用洗碗机清洗。",
        {"brand": "CrispAir", "model": "CA5.5", "colour": "Black",
         "warranty": "12 months"},
        [{"label": "Capacity", "value": "5.5 L square basket"},
         {"label": "Power", "value": "1700 W dual-zone"},
         {"label": "Presets", "value": "12 one-touch"},
         {"label": "Temp range", "value": "40-200 °C"},
         {"label": "Cleaning", "value": "Dishwasher-safe parts"}],
        [{"author": "Derek", "rating": 5,
          "comment": "Wings come out restaurant-crispy with a spray of oil. "
                     "Big enough for the whole family."},
         {"author": "Amara", "rating": 4,
          "comment": "Heats quickly and evenly. It is a bit of a counter "
                     "hog, but worth it."},
         {"author": "Leo", "rating": 5,
          "comment": "The salmon preset is foolproof. Cleanup is genuinely "
                     "two minutes."}],
        ["air fryer", "cooking", "healthy", "kitchen appliance"],
        140),
    record(
        "HOM-004", "hydropure-ultrasonic-humidifier",
        "HydroPure Ultrasonic Humidifier",
        "净润超声加湿器",
        99.99,
        "A 6-litre tank runs up to 50 hours at a whisper-quiet 26 dB, with "
        "a built-in hygrometer holding your target humidity automatically. "
        "The top-fill design refills without carrying the tank to the sink.",
        "6 升水箱以 26 分贝的静音运行长达 50 小时，内置湿度计自动维持目标湿度。"
        "上注水设计无需搬水箱即可加水。",
        {"brand": "HydroPure", "model": "HP6", "colour": "Oak White",
         "warranty": "12 months"},
        [{"label": "Tank", "value": "6 L top-fill"},
         {"label": "Runtime", "value": "Up to 50 h"},
         {"label": "Noise", "value": "26 dB whisper mode"},
         {"label": "Control", "value": "Auto humidity target"},
         {"label": "Mist", "value": "360° nozzle, cool mist"}],
        [{"author": "Fatima", "rating": 5,
          "comment": "Bedroom stays at 55 percent humidity all night and I "
                     "sleep better. Cannot hear it running."},
         {"author": "George", "rating": 4,
          "comment": "Top fill is brilliant. In hard-water areas, use "
                     "filtered water to avoid mineral dust."},
         {"author": "Wendy", "rating": 5,
          "comment": "Freed my winter skin from the radiator dryness. The "
                     "auto mode just works."}],
        ["humidifier", "bedroom", "air quality", "quiet"],
        160),
    record(
        "HOM-005", "chefedge-8-inch-chef-knife",
        "ChefEdge 8-Inch Chef Knife",
        "厨锋 8 寸主厨刀",
        119.00,
        "Forged from 67-layer Damascus steel around a VG-10 core, hardened "
        "to 61 HRC with a hand-finished edge. The full-tang octagonal handle "
        "balances at the bolster for a controlled, fatigue-free grip.",
        "大马士革 67 层锻打包裹 VG-10 钢芯，硬度 61 HRC，手工开刃。"
        "全龙骨八角刀柄在刀颈处配重平衡，握持稳定不易疲劳。",
        {"brand": "ChefEdge", "model": "CE-8", "colour": "Damascus",
         "warranty": "Lifetime against defects"},
        [{"label": "Steel", "value": "VG-10 core, 67-layer Damascus"},
         {"label": "Hardness", "value": "61 HRC"},
         {"label": "Blade", "value": "8\" (203 mm), 15° edge"},
         {"label": "Handle", "value": "Stabilised octagonal hardwood"},
         {"label": "Balance", "value": "At the bolster, full tang"}],
        [{"author": "Chef Raul", "rating": 5,
          "comment": "Paper-thin onion dices after three months of daily "
                     "line work. Holds its edge remarkably."},
         {"author": "Judith", "rating": 5,
          "comment": "The balance makes prep feel effortless. A true "
                     "step up from stamped knives."},
         {"author": "Sam", "rating": 4,
          "comment": "Beautiful and sharp; hand-wash only, but that is "
                     "expected at this steel."}],
        ["knife", "kitchen", "damascus", "cooking", "chef"],
        90),
    record(
        "HOM-006", "thermolock-500ml-vacuum-flask",
        "ThermoLock 500ml Vacuum Flask",
        "锁温 500 毫升保温杯",
        32.99,
        "Double-wall 316 stainless steel keeps drinks hot for 12 hours or "
        "iced for 24, with a leak-proof one-hand lid that locks for bag "
        "carry. The 500 ml body still fits every cup holder we tested.",
        "316 不锈钢双层真空结构，保温 12 小时、保冷 24 小时；单手开合杯盖可锁定防漏，"
        "放心放进背包。500 毫升杯身适配我们测试过的所有杯架。",
        {"brand": "ThermoLock", "model": "TL500", "colour": "Alpine Blue",
         "warranty": "12 months"},
        [{"label": "Capacity", "value": "500 ml"},
         {"label": "Heat retention", "value": "12 h"},
         {"label": "Cold retention", "value": "24 h"},
         {"label": "Steel", "value": "316 stainless, BPA free"},
         {"label": "Lid", "value": "Leak-proof, one-hand lock"}],
        [{"author": "Mateo", "rating": 5,
          "comment": "Coffee was still steaming after a morning hike. The "
                     "lock button survives being knocked around a bag."},
         {"author": "Anouk", "rating": 5,
          "comment": "Ice cubes at lunch from a 7 am fill. Zero leaks in "
                     "my laptop backpack."},
         {"author": "Ibrahim", "rating": 4,
          "comment": "Great heat retention; a wider mouth option would "
                     "make cleaning even easier."}],
        ["flask", "thermos", "insulated", "bottle", "travel", "保温"],
        500),
    record(
        "HOM-007", "lumenglow-dimmable-floor-lamp",
        "LumenGlow Dimmable Floor Lamp",
        "流光可调光落地灯",
        149.00,
        "A 140 cm linen-shaded lamp with stepless dimming from candle-warm "
        "to daylight, controllable by touch, remote or app. The weighted "
        "base and fabric-wrapped stem sit securely in reading corners.",
        "140 厘米亚麻灯罩落地灯，无级调光从烛光暖到日光色，支持触摸、遥控与 App 控制。"
        "配重底座与织物包裹灯杆，安稳立于阅读角落。",
        {"brand": "LumenGlow", "model": "LG-140", "colour": "Natural Linen",
         "warranty": "24 months"},
        [{"label": "Height", "value": "140 cm"},
         {"label": "Colour temp", "value": "2700-6000 K stepless"},
         {"label": "Dimming", "value": "Touch, remote, app"},
         {"label": "Shade", "value": "Double-layer linen"},
         {"label": "Bulb", "value": "Included 15 W LED"}],
        [{"author": "Tessa", "rating": 5,
          "comment": "The warm end of the range makes the living room feel "
                     "expensive. App scenes are handy for movie nights."},
         {"author": "Aldo", "rating": 4,
          "comment": "Solid and stable next to the armchair. Assembly took "
                     "ten minutes alone."},
         {"author": "Mei", "rating": 5,
          "comment": "Reading light bright enough, and the linen casts a "
                     "beautiful soft glow."}],
        ["lamp", "lighting", "dimmer", "living room", "decor"],
        110),
    record(
        "HOM-008", "purenest-memory-foam-pillow",
        "PureNest Memory Foam Pillow",
        "纯巢记忆棉枕头",
        59.99,
        "A ventilated gel-infused memory foam core with a contoured loft "
        "that supports side and back sleepers without pressure points. The "
        "bamboo-blend cover unzips for machine washing and sleeps cool.",
        "透气凝胶记忆棉内芯，轮廓高度贴合侧睡与仰睡曲线，无压迫点。"
        "竹纤维混纺枕套可拆洗，触感清凉。",
        {"brand": "PureNest", "model": "PN-C", "colour": "White",
         "warranty": "24 months"},
        [{"label": "Core", "value": "Gel-infused memory foam"},
         {"label": "Loft", "value": "Contoured, medium-high"},
         {"label": "Cover", "value": "Bamboo blend, washable"},
         {"label": "Size", "value": "60 x 40 x 12/10 cm"},
         {"label": "Certification", "value": "CertiPUR foam"}],
        [{"author": "Rosa", "rating": 5,
          "comment": "Neck pain from side sleeping eased within a week. It "
                     "keeps its shape night after night."},
         {"author": "Ben", "rating": 4,
          "comment": "Supportive and cool; stomach sleepers may find the "
                     "contour too high."},
         {"author": "Isabel", "rating": 5,
          "comment": "The washable cover survived a spill and came out "
                     "like new."}],
        ["pillow", "sleep", "memory foam", "bedroom", "bedding"],
        320),

    record(
        "APP-001", "trailblazer-waterproof-rain-jacket",
        "Trailblazer Waterproof Rain Jacket",
        "开路者防水冲锋衣",
        189.00,
        "A 3-layer 20K/20K waterproof-breathable shell with fully taped "
        "seams and pit zips that dump heat on climbs. The helmet-compatible "
        "hood and storm cuffs earn their keep on genuinely foul days.",
        "三层 20K/20K 防水透气面料，全压胶接缝与腋下拉链在爬升时快速散热。"
        "可容纳头盔的风帽与防风袖口，在真正的坏天气里物尽其用。",
        {"brand": "Trailblazer", "model": "TB-3L", "colour": "Moss Green",
         "warranty": "Lifetime"},
        [{"label": "Waterproofing", "value": "20K mm / 20K g breathability"},
         {"label": "Seams", "value": "Fully taped"},
         {"label": "Venting", "value": "Pit zips"},
         {"label": "Hood", "value": "Helmet-compatible, 3-way adjust"},
         {"label": "Weight", "value": "410 g (M)"}],
        [{"author": "Hillwalker Jo", "rating": 5,
          "comment": "Six hours of Scottish drizzle, bone dry inside. Pit "
                     "zips are the unsung hero."},
         {"author": "Marc", "rating": 5,
          "comment": "Packs into its own pocket and still breathes on "
                     "steep ascents. True 3-layer value."},
         {"author": "Priyanka", "rating": 4,
          "comment": "Excellent shell; sizing runs slightly long in the "
                     "body, size accordingly."}],
        ["rain jacket", "waterproof", "hiking", "outdoor", "shell"],
        130,
        apparel={
            "sizing": {"sizes": ["XS", "S", "M", "L", "XL", "XXL"],
                       "fit": "Regular; room for a midlayer"},
            "care": "Machine wash warm, tumble dry low to reactivate DWR; "
                    "no fabric softener.",
            "materials": {"shell": "100% recycled nylon 3L laminate",
                          "lining": "None (3-layer construction)"},
        }),
    record(
        "APP-002", "cloudstep-everyday-merino-sneakers",
        "CloudStep Everyday Merino Sneakers",
        "云步日常美利奴休闲鞋",
        139.00,
        "An 17 mm supercritical foam midsole with a merino wool upper that "
        "breathes in summer and insulates in winter. Machine washable, and "
        "the outsole is quietly grippy on wet pavement.",
        "超临界发泡 17 毫米中底搭配美利奴羊毛鞋面，夏透气冬保暖。"
        "整机可机洗，湿滑路面亦有出色抓地力。",
        {"brand": "CloudStep", "model": "CS-M", "colour": "Oat",
         "warranty": "12 months"},
        [{"label": "Midsole", "value": "Supercritical foam, 17 mm"},
         {"label": "Upper", "value": "Merino wool blend"},
         {"label": "Drop", "value": "6 mm"},
         {"label": "Weight", "value": "232 g (EU 42)"},
         {"label": "Care", "value": "Machine washable"}],
        [{"author": "Nils", "rating": 5,
          "comment": "All-day city walking, zero break-in. Threw them in "
                     "the wash and they came out like new."},
         {"author": "Camille", "rating": 4,
          "comment": "Beautifully comfortable; sizing is generous, go half "
                     "a size down."},
         {"author": "Owen", "rating": 5,
          "comment": "The wool keeps them odor-free even without socks in "
                     "summer. Genuinely all-season."}],
        ["sneakers", "merino", "walking", "everyday", "comfort"],
        210,
        apparel={
            "sizing": {"sizes": ["EU 36", "EU 37", "EU 38", "EU 39", "EU 40",
                                 "EU 41", "EU 42", "EU 43", "EU 44", "EU 45"],
                       "fit": "Roomy; take half a size down"},
            "care": "Machine wash cold on gentle, air dry; do not tumble "
                    "dry.",
            "materials": {"upper": "72% merino wool, 28% recycled PET",
                          "outsole": "Natural rubber"},
        }),
    record(
        "APP-003", "heritage-leather-bifold-wallet",
        "Heritage Leather Bifold Wallet",
        "传承真皮对折钱包",
        79.00,
        "Full-grain vegetable-tanned leather cut from a single hide, "
        "saddle-stitched with waxed thread at eight stitches per inch. Six "
        "card slots and two bill compartments, and it Patinas beautifully.",
        "整张植鞣全粒面皮革裁制，蜡线鞍缝每英寸八针。六个卡位与两个钞位，"
        "随岁月形成漂亮包浆。",
        {"brand": "Heritage", "model": "HB-6", "colour": "Chestnut",
         "warranty": "Lifetime stitching"},
        [{"label": "Leather", "value": "Full-grain vegetable-tanned"},
         {"label": "Stitching", "value": "Hand saddle-stitched"},
         {"label": "Capacity", "value": "6 cards + 2 bill pockets"},
         {"label": "Thickness", "value": "9 mm empty"},
         {"label": "RFID", "value": "Blocking liner"}],
        [{"author": "Frank", "rating": 5,
          "comment": "Three years in and the patina is gorgeous. Stitching "
                     "has not moved a millimetre."},
         {"author": "Dana", "rating": 5,
          "comment": "Slimmer than my old wallet while holding more. The "
                     "leather smells incredible."},
         {"author": "Sung", "rating": 4,
          "comment": "Stiff for the first fortnight, then it molds to your "
                     "pocket perfectly."}],
        ["wallet", "leather", "accessory", "everyday carry"],
        240,
        apparel={
            "sizing": {"sizes": ["One size"], "fit": "12 x 9.5 cm closed"},
            "care": "Condition with leather balm twice a year; keep dry.",
            "materials": {"shell": "Full-grain vegetable-tanned cowhide",
                          "liner": "RFID-blocking fabric"},
        }),
    record(
        "APP-004", "summitlayer-merino-base-layer",
        "SummitLayer Merino Base Layer Top",
        "峰层美利奴打底上衣",
        95.00,
        "18.5-micron merino knit at 200 g/m2 regulates temperature from "
        "trailhead to summit and resists odor across a multi-day trip. Flat "
        "lock seams sit comfortably under a pack's straps.",
        "18.5 微米美利奴针织面料（200 g/m²），从山脚到山顶调节体温，"
        "多日行程不易产生异味。平锁接缝在背包肩带下依然舒适。",
        {"brand": "SummitLayer", "model": "SL-200", "colour": "Deep Teal",
         "warranty": "12 months"},
        [{"label": "Fabric weight", "value": "200 g/m2"},
         {"label": "Fibre", "value": "18.5 micron merino"},
         {"label": "Seams", "value": "Flat lock"},
         {"label": "Odour control", "value": "Natural (merino)"},
         {"label": "Use", "value": "Hiking, ski, travel"}],
        [{"author": "Erik", "rating": 5,
          "comment": "Four days of hut-to-hut hiking without washing and "
                     "it stayed fresh. Warm when damp, too."},
         {"author": "Alina", "rating": 5,
          "comment": "No itch at all, which my skin usually notices "
                     "immediately. My default ski layer now."},
         {"author": "Tomas", "rating": 4,
          "comment": "Excellent regulation; sleeves run long which I "
                     "actually prefer for gloves."}],
        ["base layer", "merino", "hiking", "thermal", "travel"],
        170,
        apparel={
            "sizing": {"sizes": ["XS", "S", "M", "L", "XL"],
                       "fit": "Athletic; size up for layering"},
            "care": "Machine wash cold, lay flat to dry; do not bleach.",
            "materials": {"shell": "100% merino wool (18.5 micron)",
                          "trim": "Recycled polyester mesh panels"},
        }),
    record(
        "APP-005", "vista-uv400-polarized-sunglasses",
        "Vista UV400 Polarized Sunglasses",
        "远景 UV400 偏光太阳镜",
        119.00,
        "Polarized nylon lenses cut glare off water and asphalt while "
        "block 100 percent of UVA/UVB, in a 24 g Italian acetate frame with "
        "spring hinges. Prescription-ready and shipped with a hard case.",
        "偏光尼龙镜片消除水面与路面眩光，100% 阻隔 UVA/UVB；"
        "24 克意大利醋酸纤维镜架配弹簧铰链，支持验光配片，附硬质眼镜盒。",
        {"brand": "Vista", "model": "VS-400", "colour": "Tortoise",
         "warranty": "12 months"},
        [{"label": "Lens", "value": "Polarized nylon, UV400"},
         {"label": "Frame", "value": "Italian acetate, 24 g"},
         {"label": "Hinges", "value": "Spring steel"},
         {"label": "Fit", "value": "Medium-wide, universal nose pads"},
         {"label": "Included", "value": "Hard case + microfibre"}],
        [{"author": "Marina", "rating": 5,
          "comment": "Sailing with these is a different sport — water "
                     "glare is gone entirely."},
         {"author": "Diego", "rating": 4,
          "comment": "Light and comfortable for long drives. Wish the case "
                     "were slimmer."},
         {"author": "Alex", "rating": 5,
          "comment": "Colour fidelity is neutral, not overly tinted. My "
                     "optician fitted prescription lenses easily."}],
        ["sunglasses", "polarized", "uv400", "driving", "accessory"],
        260,
        apparel={
            "sizing": {"sizes": ["One size"], "fit": "Lens 54 mm, bridge 18 mm"},
            "care": "Clean with the microfibre cloth; store in the case.",
            "materials": {"frame": "Italian acetate cellulose",
                          "lens": "Polarized nylon, UV400"},
        }),
    record(
        "APP-006", "urban-carry-20l-commuter-backpack",
        "Urban Carry 20L Commuter Backpack",
        "都市行者 20 升通勤双肩包",
        89.00,
        "A clamshell 20-litre pack with a floating, false-bottom laptop "
        "pouch that survived our 1-metre drop test. Weatherproof 900D "
        "recycled shell, luggage pass-through, and a quick-grab top pocket "
        "for transit cards.",
        "20 升对开式通勤包，悬浮双层底笔记本袋通过 1 米跌落测试。"
        "900D 再生面料防泼水，附行李箱拉杆穿透带与随手取放的交通卡顶袋。",
        {"brand": "Urban Carry", "model": "UC-20", "colour": "Charcoal",
         "warranty": "24 months"},
        [{"label": "Volume", "value": "20 L clamshell"},
         {"label": "Laptop", "value": "Up to 16\", floating pouch"},
         {"label": "Fabric", "value": "900D recycled, DWR coated"},
         {"label": "Weight", "value": "890 g"},
         {"label": "Extras", "value": "Luggage pass-through, bottle pocket"}],
        [{"author": "Viktor", "rating": 5,
          "comment": "The false-bottom laptop pouch has saved my machine "
                     "twice from drops. Organizes a full workday perfectly."},
         {"author": "Jenna", "rating": 4,
          "comment": "Sits flat on the back, looks smart in meetings. Side "
                     "pocket fits a 1-litre bottle snugly."},
         {"author": "Pablo", "rating": 5,
          "comment": "Clamshell opening means nothing gets lost at the "
                     "bottom. Commuter essential."}],
        ["backpack", "commuter", "laptop", "urban", "travel"],
        190,
        apparel={
            "sizing": {"sizes": ["One size"], "fit": "45 x 30 x 15 cm"},
            "care": "Spot clean with damp cloth; air dry.",
            "materials": {"shell": "900D recycled polyester, DWR",
                          "lining": "210D recycled nylon"},
        }),
    record(
        "APP-007", "silkroute-mulberry-silk-scarf",
        "SilkRoute Mulberry Silk Scarf",
        "丝路桑蚕丝围巾",
        65.00,
        "A 110 x 110 cm square of 22-momme mulberry silk twill, hand-rolled "
        "and stitched at the edges. Warm as a layer, light as a whisper, "
        "and it packs to the size of a plum.",
        "110 x 110 厘米 22 姆米桑蚕丝斜纹方巾，手工卷边缝制。"
        "保暖如一层薄衣，轻盈似一声耳语，收纳后仅李子大小。",
        {"brand": "SilkRoute", "model": "SR-22", "colour": "Imperial Red",
         "warranty": "6 months"},
        [{"label": "Material", "value": "22-momme mulberry silk twill"},
         {"label": "Size", "value": "110 x 110 cm"},
         {"label": "Edges", "value": "Hand-rolled and stitched"},
         {"label": "Weight", "value": "68 g"}],
        [{"author": "Colette", "rating": 5,
          "comment": "The twill has a beautiful liquid drape and the red "
                     "is deep, not brassy. Elevates every coat."},
         {"author": "Anja", "rating": 5,
          "comment": "Silk keeps my neck warm on the bike and vanishes "
                     "into a bag pocket."},
         {"author": "Rosa", "rating": 4,
          "comment": "Gorgeous gift packaging. Hand wash only, but that "
                     "is normal for real silk."}],
        ["scarf", "silk", "accessory", "gift", "fashion"],
        280,
        apparel={
            "sizing": {"sizes": ["One size"], "fit": "110 x 110 cm square"},
            "care": "Hand wash cold with silk detergent; hang to dry.",
            "materials": {"shell": "100% mulberry silk, 22 momme"},
        }),

    record(
        "SPO-001", "summit-65l-trekking-backpack",
        "Summit 65L Trekking Backpack",
        "峰顶 65 升徒步背包",
        159.00,
        "An anti-gravity perimeter frame carries 20 kg so well you forget "
        "it, with an adjustable torso (16-21 in) and a floating lid. Six "
        "external pockets and dual ice-tool loops round out a genuine "
        "multiday workhorse.",
        "反重力外框背负系统在 20 公斤负载下近乎无感，背长 16-21 英寸可调，"
        "浮动顶盖设计。六个外部口袋与双冰镐挂环，是真正的多日徒步主力。",
        {"brand": "Summit", "model": "S65", "colour": "Basalt Grey",
         "warranty": "Lifetime"},
        [{"label": "Volume", "value": "65 L (+10 L lid)"},
         {"label": "Frame", "value": "Perimeter anti-gravity"},
         {"label": "Torso", "value": "Adjustable 16-21 in"},
         {"label": "Weight", "value": "1.9 kg"},
         {"label": "Access", "value": "Top, front, sleeping bag"}],
        [{"author": "Thru-hiker Rae", "rating": 5,
          "comment": "Nine days on the trail, zero hot spots. The floating "
                     "lid swallowed a bear canister setup."},
         {"author": "Milan", "rating": 5,
          "comment": "Load transfer to the hips is superb — 18 kg felt "
                     "like 12. Adjusting torso length took minutes."},
         {"author": "Pia", "rating": 4,
          "comment": "Brilliant carry; wish the hip belt pockets were "
                     "slightly larger for a phone."}],
        ["backpack", "trekking", "hiking", "multiday", "outdoor"],
        85),
    record(
        "SPO-002", "flowstate-yoga-mat-6mm",
        "FlowState Yoga Mat 6mm",
        "心流瑜伽垫 6 毫米",
        49.99,
        "A 6 mm natural-rubber mat with a moisture-wicking polyurethane top "
        "that grips harder as you sweat. Alignment lines are subtly "
        "debossed, and it lies flat out of the box without curling.",
        "6 毫米天然橡胶瑜伽垫，吸湿聚氨酯面层越出汗越防滑。"
        "体式对齐线以压纹呈现，开箱即平铺不卷边。",
        {"brand": "FlowState", "model": "FS-6", "colour": "Sage",
         "warranty": "12 months"},
        [{"label": "Thickness", "value": "6 mm"},
         {"label": "Material", "value": "Natural rubber + PU top"},
         {"label": "Size", "value": "183 x 68 cm"},
         {"label": "Weight", "value": "2.3 kg"},
         {"label": "Grip", "value": "Increases when wet"}],
        [{"author": "Hot-yoga Hannah", "rating": 5,
          "comment": "Sweat-soaked bikram classes and my hands never "
                     "slipped once. No rubber smell after airing."},
         {"author": "Deniz", "rating": 4,
          "comment": "Cushioning is great for knees; at 2.3 kg it is a "
                     "studio mat more than a carry-everywhere mat."},
         {"author": "Karin", "rating": 5,
          "comment": "The alignment lines genuinely improved my down dog "
                     "consistency."}],
        ["yoga", "mat", "fitness", "pilates", "exercise"],
        230),
    record(
        "SPO-003", "irongrip-adjustable-dumbbell-24kg",
        "IronGrip Adjustable Dumbbell 24kg",
        "铁握可调节哑铃 24 公斤",
        199.00,
        "Dial from 2.5 to 24 kg in 2.5 kg steps with a twist of the handle; "
        "the locking plates seat with a satisfying click and never rattle. "
        "One dumbbell replaces a whole rack in a home gym corner.",
        "旋转手柄即可在 2.5-24 公斤间以 2.5 公斤步进调节；锁片到位咔哒作响、纹丝不晃。"
        "一只哑铃替代整面哑铃架，适配家庭健身角。",
        {"brand": "IronGrip", "model": "IG-24", "colour": "Black",
         "warranty": "24 months"},
        [{"label": "Range", "value": "2.5-24 kg in 2.5 kg steps"},
         {"label": "Mechanism", "value": "Dial lock plates"},
         {"label": "Handle", "value": "Knurled steel, 33 mm"},
         {"label": "Footprint", "value": "44 x 22 cm tray"},
         {"label": "Sold as", "value": "Single dumbbell + tray"}],
        [{"author": "HomeGym Harriet", "rating": 5,
          "comment": "Supersets are finally quick — the dial turns in under "
                     "two seconds and nothing wobbles."},
         {"author": "Oskar", "rating": 4,
          "comment": "Solid mechanism; the tray is long, so measure your "
                     "shelf. Plates feel secure at every weight."},
         {"author": "Renata", "rating": 5,
          "comment": "Replaced five pairs of dumbbells. Drop it on carpet "
                     "(accidentally, once) and it shrugged it off."}],
        ["dumbbell", "strength", "home gym", "adjustable", "weights"],
        95),
    record(
        "SPO-004", "cascade-2-person-camping-tent",
        "Cascade 2-Person Camping Tent",
        "叠瀑双人露营帐篷",
        229.00,
        "A 2.9 kg freestanding dome with dual vestibules and a full-coverage "
        "fly rated 3000 mm. Colour-coded poles pitch in under four minutes, "
        "and two doors end the climbing-over-your-partner era.",
        "2.9 公斤自由站立穹顶帐篷，双前庭，外帐防水 3000 毫米。"
        "彩色编码帐杆四分钟内搭建完成，双门设计告别跨过同伴出门的日子。",
        {"brand": "Cascade", "model": "C2", "colour": "Ember Orange",
         "warranty": "24 months"},
        [{"label": "Capacity", "value": "2 person"},
         {"label": "Weight", "value": "2.9 kg packed"},
         {"label": "Fly", "value": "3000 mm PU coated"},
         {"label": "Doors", "value": "2, dual vestibules"},
         {"label": "Pitch time", "value": "< 4 min, colour-coded"}],
        [{"author": "Fernwalker", "rating": 5,
          "comment": "Rode out a night of 60 km/h gusts in the Pyrenees. "
                     "Vestibules fit both packs plus boots."},
         {"author": "Sam & Jo", "rating": 5,
          "comment": "Two doors is civilised. Pitch is genuinely under "
                     "four minutes even at dusk in light rain."},
         {"author": "Andrea", "rating": 4,
          "comment": "Light and sturdy; condensation under the fly in "
                     "still humid air, like every double-wall tent."}],
        ["tent", "camping", "outdoor", "backpacking", "2 person"],
        75),
    record(
        "SPO-005", "hydraflow-insulated-sports-bottle-750ml",
        "HydraFlow Insulated Sports Bottle 750ml",
        "活力保温运动水壶 750 毫升",
        29.99,
        "A 750 ml sports bottle with a high-flow bite valve that keeps "
        "water icy for 18 hours in a rubberised, cage-friendly body. The "
        "valve strips apart for real cleaning, not just rinsing.",
        "750 毫升运动水壶，高流量咬嘴阀盖，橡胶握身适配车架水壶架，保冷 18 小时。"
        "阀盖可完全拆解清洗，不只是简单冲一冲。",
        {"brand": "HydraFlow", "model": "HF750", "colour": "Volt Yellow",
         "warranty": "12 months"},
        [{"label": "Capacity", "value": "750 ml"},
         {"label": "Cold retention", "value": "18 h (double wall)"},
         {"label": "Valve", "value": "High-flow bite, strippable"},
         {"label": "Body", "value": "Rubberised steel, cage friendly"},
         {"label": "Diameter", "value": "74 mm"}],
        [{"author": "Roadie Cal", "rating": 5,
          "comment": "Ice survives century rides in summer. Flow rate is "
                     "big enough to actually drink at speed."},
         {"author": "Tri-Kat", "rating": 4,
          "comment": "Fits every bottle cage I own. Taking the valve "
                     "apart monthly keeps it fresh."},
         {"author": "Boulders Ash", "rating": 5,
          "comment": "Rubberised body dents invisibly when dropped on "
                     "crag floors. Still insulated perfectly."}],
        ["bottle", "cycling", "sports", "hydration", "insulated"],
        340),
    record(
        "SPO-006", "pacekeeper-gps-running-watch",
        "PaceKeeper GPS Running Watch",
        "配速守护 GPS 跑步手表",
        149.00,
        "A featherweight 38 g running watch with multi-band GPS, live "
        "pace-band alerts and 26 hours of logged GPS battery. Training "
        "load, VO2 trend and race predictor come standard, no subscription.",
        "38 克轻量跑步手表，多频 GPS、实时配速带提醒与 26 小时 GPS 续航。"
        "训练负荷、VO2 趋势与比赛成绩预测均为标配，无需订阅。",
        {"brand": "PaceKeeper", "model": "PK-Run", "colour": "Storm Blue",
         "warranty": "24 months"},
        [{"label": "GPS", "value": "Multi-band, 5 systems"},
         {"label": "Weight", "value": "38 g"},
         {"label": "Battery", "value": "26 h GPS, 9 days smart"},
         {"label": "Metrics", "value": "Pace band, load, VO2, race predictor"},
         {"label": "Straps", "value": "Quick-release 20 mm"}],
        [{"author": "Marathoner Didi", "rating": 5,
          "comment": "Pace band buzzing when I drift is my favourite "
                     "coach. GPS lock under bridges is far better than my "
                     "old watch."},
         {"author": "TrailTom", "rating": 5,
          "comment": "Multi-band GPS holds a line in canyon sections where "
                     "others drift. 26 hours covers my 100k ambition."},
         {"author": "5k Sam", "rating": 4,
          "comment": "Everything a runner needs without a subscription "
                     "tax. Screen is a touch dim in direct sun."}],
        ["running", "gps watch", "training", "marathon", "sport"],
        150),
    record(
        "SPO-007", "flexband-resistance-band-set",
        "FlexBand Resistance Band Set",
        "劲弹阻力带套装",
        34.99,
        "Five layered latex bands from 5 to 60 kg of resistance, with "
        "door anchor, handles and ankle straps for a complete gym in a "
        "pouch. Colour-coded sleeves stop the roll-up guessing game.",
        "五层乳胶阻力带覆盖 5-60 公斤，配门锚、手柄与脚踝带，一只收纳袋就是完整健身房。"
        "彩色外套标识阻力等级，卷收不再靠猜。",
        {"brand": "FlexBand", "model": "FB-5", "colour": "Multi",
         "warranty": "12 months"},
        [{"label": "Resistance", "value": "5, 15, 25, 40, 60 kg"},
         {"label": "Material", "value": "Layered natural latex"},
         {"label": "Accessories", "value": "Door anchor, 2 handles, 2 straps"},
         {"label": "Pouch", "value": "Included mesh carry bag"}],
        [{"author": "Traveller Nell", "rating": 5,
          "comment": "Hotel-room workouts that actually load the back and "
                     "shoulders. Door anchor holds firm."},
         {"author": "Physio Max", "rating": 5,
          "comment": "I prescribe these in clinic — the incremental "
                     "resistance steps are perfectly judged."},
         {"author": "Priya", "rating": 4,
          "comment": "Great bands; powder smell aired out in a day. The "
                     "pouch zips smoothly."}],
        ["resistance bands", "fitness", "home gym", "travel", "strength"],
        380),
]
