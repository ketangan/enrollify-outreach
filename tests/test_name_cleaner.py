from src.name_cleaner import clean_school_name


def test_clean_school_name_removes_legal_suffixes_and_noise():
    assert clean_school_name("Compton Cowboys, Inc") == "Compton Cowboys"
    assert clean_school_name("Olive Tree Learning Academy Inc") == "Olive Tree Learning Academy"
    assert clean_school_name("Tree House Kids #2 Daycare") == "Tree House Kids Daycare"
    assert clean_school_name("Claddagh Dance Company") == "Claddagh Dance Company"


def test_clean_school_name_title_cases_all_caps_without_shouting():
    assert clean_school_name("DUNGEON OF DISCIPLINE GYM") == "Dungeon of Discipline Gym"
    assert clean_school_name("STREET SPORTS JIU-JITSU") == "Street Sports Jiu-Jitsu"
    assert clean_school_name("UPRISE MMA") == "Uprise MMA"
    assert clean_school_name("PEPE'S SPORTS") == "Pepe's Sports"


def test_clean_school_name_removes_trailing_location_suffixes():
    assert clean_school_name("10th Planet Jiu Jitsu - West LA") == "10th Planet Jiu Jitsu"
    assert clean_school_name("10th Planet Jiu Jitsu - Downtown Los Angeles") == "10th Planet Jiu Jitsu"
    assert (
        clean_school_name("Power of One Self-Defense - Long Beach", city="Long Beach", state="CA")
        == "Power of One Self-Defense"
    )
    assert (
        clean_school_name("Some Preschool Los Angeles", city="Los Angeles", state="CA")
        == "Some Preschool"
    )
    assert (
        clean_school_name("CODELA Preschool Hawthorne C.D.C", city="Hawthorne", state="CA")
        == "CODELA Preschool"
    )
    assert (
        clean_school_name("Le Petit Gan International Preschool West Hollywood")
        == "Le Petit Gan International Preschool"
    )
    assert (
        clean_school_name("Bessie Pregerson Child Development Center", city="Los Angeles", state="CA")
        == "Bessie Pregerson Child Development Center"
    )
    assert (
        clean_school_name("Alliance Française de Los Angeles", city="Los Angeles", state="CA")
        == "Alliance Française de Los Angeles"
    )
    assert (
        clean_school_name("Ballet School of Long Beach", city="Long Beach", state="CA")
        == "Ballet School of Long Beach"
    )


def test_clean_school_name_removes_trailing_seo_service_descriptors():
    assert (
        clean_school_name("Living Tango - Argentine Tango lessons, Coaching & Wedding Dance prep")
        == "Living Tango"
    )
    assert (
        clean_school_name("ABC Music Academy - Piano Lessons, Voice Classes & Summer Camps")
        == "ABC Music Academy"
    )
    assert (
        clean_school_name("Tiny Scholars Preschool: Daycare, Preschool & After School Care")
        == "Tiny Scholars Preschool"
    )
    assert (
        clean_school_name("The Center: A Place For Children")
        == "The Center: A Place For Children"
    )


def test_clean_school_name_removes_non_english_alternate_names():
    assert (
        clean_school_name("Jung Im Lee Korean Dance Academy | 이정임 한국무용 아카데미")
        == "Jung Im Lee Korean Dance Academy"
    )
    assert (
        clean_school_name("Edupro Academy(에듀프로 아카데미/ Tutoring/ SAT/ ACT/ College Consulting)")
        == "Edupro Academy"
    )


def test_clean_school_name_preserves_accented_latin_names():
    assert clean_school_name("Colibrí Spanish Immersion Playschool") == "Colibrí Spanish Immersion Playschool"
    assert clean_school_name("Alliance Française de Los Angeles") == "Alliance Française de Los Angeles"
    assert clean_school_name("Perez Family Child Care🧸") == "Perez Family Child Care"
    assert clean_school_name("Just 4 Kidd’s Family Daycare LLC") == "Just 4 Kidd's Family Daycare"
    assert clean_school_name("GymRatz L.A.") == "GymRatz L.A."
