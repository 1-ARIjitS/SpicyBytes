import streamlit as st
import pandas as pd
import os
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, trim, regexp_replace
import configparser
import json
import math
import folium
from streamlit_folium import st_folium, folium_static
from bokeh.models import CustomJS, Button
from streamlit_bokeh_events import streamlit_bokeh_events


# For running through gcs
root_dir = os.path.abspath(os.path.join(os.getcwd()))

# Specify the path to config file
config_file_path = os.path.join(root_dir, "config.ini")
config = configparser.ConfigParser()
config.read(config_file_path)

config_file_path_json = os.path.join(root_dir, "config.json")
with open(config_file_path_json) as f:
    config_json = json.load(f)

# Set page config for a better appearance
# st.set_page_config(page_title="Shortest Path", layout="wide")

# Title of the app
st.title('Closest Supermarkets')

# Specify the path to the GCS Parquet file
formatted_zone_bucket = config["GCS"]["formatted_bucket_name"]
gcs_parquet_path = 'gs://'+formatted_zone_bucket + '/'

# Function to load data from GCS
@st.cache_data
def load_data_from_gcs(filepath):
    spark = SparkSession.builder \
        .appName("Feature 5") \
        .config("spark.jars.packages", "com.google.cloud.bigdataoss:gcs-connector:hadoop3-2.2.2") \
        .config("fs.gs.impl", "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem") \
        .config("fs.AbstractFileSystem.gs.impl", "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS") \
        .config("google.cloud.auth.service.account.json.keyfile", os.path.join(root_dir,"gcs_config.json")) \
        .getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    
    df = spark.read.parquet(filepath)
    
    # Convert PySpark DataFrame to Pandas DataFrame
    return df.toPandas()

def haversine(lat1, lon1, lat2, lon2):
    # calculates distance between two coordinates
    R = 6371  # Radius of the Earth in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c
    return distance


def closest_supermarkets(df_supermarkets, df_user_location):
    # Returns dataframe containing closest supermarkets to chosen customer
    df_supermarkets['Distance From Customer'] = df_supermarkets.apply(
    lambda row: haversine(df_user_location['Latitude'], 
                          df_user_location['Longitude'], 
                          row['Latitude'], row['Longitude']),
    axis=1)
    return df_supermarkets.sort_values(by='Distance From Customer').head(5)

def get_current_location():

    # Define the Bokeh button for triggering the geolocation
    loc_button = Button(label="Use Current Location")
    loc_button.js_on_event("button_click", CustomJS(code="""
    navigator.geolocation.getCurrentPosition(
        (location) => {
            const latitude = location.coords.latitude;
            const longitude = location.coords.longitude;
            document.dispatchEvent(new CustomEvent("GET_LOCATION", {detail: {latitude, longitude}}))
        }
    )
    """))

    # Create a Bokeh event for geolocation
    result = streamlit_bokeh_events(
        loc_button,
        events="GET_LOCATION",
        key="get_location",  # Unique identifier for this event
        refresh_on_update=False,
        override_height=75,
        debounce_time=0
    )

    # Display the latitude and longitude only if the button is clicked
    if result and "GET_LOCATION" in result:
            latitude = result["GET_LOCATION"]["latitude"]
            longitude = result["GET_LOCATION"]["longitude"]
            # st.write(latitude, longitude)
            return (latitude, longitude)
    return None


def add_marker_customer(df, color, mymap):
    # Adds marker for customer
    for _, row in df.iterrows():
        folium.Marker(
            location=[row['Latitude'], row['Longitude']],
            popup="Usted esta aqui!",
            icon=folium.Icon(color=color)
        ).add_to(mymap)

def plot_supermarkets(df_all_supermarket_location):
    df_plot = df_all_supermarket_location.sample(n=50, random_state=42) # Taking a sample because there are too many points.

    # Initialize the map centered around the mean latitude and longitude
    mymap = folium.Map(location=[df_plot['latitude'].mean(), df_plot['longitude'].mean()], zoom_start=13)

    # Add points to the map
    for _, row in df_plot.iterrows():
        folium.Marker(
            location=[row['Latitude'], row['Longitude']],
            popup=row['commercial_name']
        ).add_to(mymap)
    folium_static(mymap, width=1000, height=600)


def add_supermarket_marker_line_customer(mymap, customer, supermarket):
    # Function to add a supermarket marker and a line between the customer and the supermarket with distance popup
    # Add supermarket marker

    marker = folium.Marker(
        location=[supermarket['Latitude'], supermarket['Longitude']],
        popup=supermarket['Store Name'] + '\n' + str("{:.2f}".format(supermarket['Distance From Customer']) + ' KM'),
        icon=folium.Icon(color='blue')
        )
    marker.add_to(mymap)

    
    # Add a line between the customer and the supermarket
    line = folium.PolyLine(
        locations=[
            [customer['Latitude'], customer['Longitude']],
            [supermarket['Latitude'], supermarket['Longitude']]
        ],
        color='blue',
        dash_array = '10'
    ).add_to(mymap)


def display_closest_supermarkets(df_chosen_customer, df_closest_supermarkets):
    
    # Initialize the map centered around the mean latitude and longitude of the customer
    mymap = folium.Map(location=[df_chosen_customer['Latitude'].mean(), df_chosen_customer['Longitude'].mean()], zoom_start=13)
    add_marker_customer(df_chosen_customer, 'red', mymap) # plot customer

    # Add supermarkets and dashed lines to the map from the customer
    for _, supermarket in df_closest_supermarkets.iterrows():
        add_supermarket_marker_line_customer(mymap, df_chosen_customer.iloc[0], supermarket)

    # Add a line between the customer and the closest supermarket
    line = folium.PolyLine(
        locations=[
            [df_chosen_customer['Latitude'].iloc[0], df_chosen_customer['Longitude'].iloc[0]],
            [df_closest_supermarkets['Latitude'].iloc[0], df_closest_supermarkets['Longitude'].iloc[0]]
        ],
        color='green',
    ).add_to(mymap)

    folium_static(mymap, width=700, height=300)

# Function to reset user input
def reset_user_input():
    st.session_state.user_input = ''

def filter_dataframe(df, query):
    return df[df['Product Name'].str.contains(query, case=False, na=False)]

def button_clicked():
    # Create a button and store its state
    if st.button('Get User Current Location'):
        return True
    return None

def closest_supermarket():
    st.write("<br>", unsafe_allow_html=True) 

    # import data using pandas if needed
    # root_dir = os.path.abspath(os.path.join(os.getcwd()))
    # data_file_path = os.path.join(root_dir,'data', 'aryan_pandas') # These are the csv files generated from the formatted zone. Not Raw files.
    # df_supermarket_products = pd.read_csv(os.path.join(data_file_path, 'supermarket_products.csv'),encoding='cp1252')
    # df_all_supermarket_location = pd.read_csv(os.path.join(data_file_path,'establishments_catalonia.csv'))

    # importing data from gcs
    df_supermarket_products = load_data_from_gcs(gcs_parquet_path + 'supermarket_products*')
    df_all_supermarket_location = load_data_from_gcs(gcs_parquet_path + 'establishments_catalonia*')

    df_supermarket_products.rename(columns={
        "store_id":"Store Id",
        "store_name":"Store Name",
        "full_address":"Full Address",
        "latitude":"Latitude",
        "longitude":"Longitude",
        "product_id":"Product Id",
        "product_name":"Product Name",
        "product_price":"Product Price",
        "quantity":"Quantity",
        "expiry_date":"Expiry Date",
    }, inplace=True)

    df_all_supermarket_location.rename(columns={
        "id":"Store Id",
        "commercial_name":"Commercial Name",
        "country_code":"Country Code",
        "full_address": "Full Address",
        "latitude": "Latitude",
        "longitude":"Longitude"
    }, inplace=True)


    # merge data for supermarkets
    df_supermarkets = pd.merge(df_supermarket_products, df_all_supermarket_location, left_on='Store Id', right_on='Store Id', how='left')
    df_supermarkets_location = df_supermarkets.drop_duplicates(subset=['Store Id']) # keeping unique locations
    df_supermarkets_location = df_supermarkets_location[['Store Id', 'Store Name', 'Full Address', 'Latitude', 'Longitude']]
    df_supermarkets_location.reset_index(drop=True, inplace=True)
    df_supermarkets = df_supermarkets[['Store Id', 'Store Name', 'Product Id', 'Product Name', 'Product Price', 'Quantity',
                                                      'Expiry Date']]
    df_supermarkets.reset_index(drop=True, inplace=True)

    # df_supermarkets has all the near expiry products being sold by supermarkets
    # df_supermarkets_location has the location of the above supermarkets
    # df_all_supermarket_location has all the available supermarkets in the region


    # location = get_current_location() # this is for using user location but its not working in anyone els's system
    latitude = 41.38990789437025
    longitude =  2.114588283932649
    location = (latitude, longitude)

    result = button_clicked()

    # if location:
    result = True
    if result:
        df_user_location = pd.DataFrame({'Latitude': [location[0]], 'Longitude': [location[1]]})
        # st.write(df_user_location)
        df_user_location = df_user_location.iloc[[0]] # only keep the first row for the user in the dataframe
        st.write("Customer Location")
        st.write(location)
        # st.write(f"Latitude: {df_user_location['latitude'][0]}, Longitude: {df_user_location['longitude'][0]}")
        st.write("Searching for supermarket deals near you..")
        df_closest_supermarkets = closest_supermarkets(df_supermarkets_location, df_user_location)
        # st.write(df_closest_supermarkets)
        # display_closest_supermarkets(df_user_location, df_closest_supermarkets)

        col1, col2 = st.columns(2)  # Create two columns

        with col1:
            st.write("Closest Supermarkets Data")
            st.dataframe(df_closest_supermarkets, use_container_width=True)  # Display the dataframe in the first column

        with col2:
            st.write("Display Closest Supermarkets")
            display_closest_supermarkets(df_user_location, df_closest_supermarkets)


        # Initialize session state for dropdown and user input if not already initialized
        if 'dropdown_selection' not in st.session_state:
            st.session_state.user_input = ''
        options = ['Show All'] + df_closest_supermarkets['Store Name'].tolist()  # to display in dropdown
        selected_market = st.selectbox("Select Supermarket", options, key='dropdown_selection', on_change=reset_user_input)

        user_query = st.text_input('Search for a product', value=st.session_state.user_input, key='user_input')
        if selected_market == "Show All":
            filtered_df = df_supermarkets.reset_index(drop=True)[['Store Name', 'Product Id', 'Product Name', 'Product Price', 'Quantity', 'Expiry Date']]
        else:
            filtered_df = df_supermarkets.query("`Store Name` == @selected_market").reset_index(drop=True)[['Product Id', 'Product Name', 'Product Price', 'Quantity', 'Expiry Date']]
        if user_query:
            filtered_df = filter_dataframe(filtered_df, user_query)

        st.dataframe(filtered_df.reset_index(drop=True), use_container_width=True)

    else:
        st.write('Click the button to allow access to your location.')

    # Custom CSS for footer
    st.markdown("""
        <style>
            footer {visibility: hidden;}
            .footer {
                position: fixed;
                left: 0;
                bottom: 0;
                width: 100%;
                background-color: #f1f1f1;
                color: black;
                text-align: center;
            }
        </style>
        <div class="footer">
            <p>@Developed by SpicyBytes</p>
        </div>
    """, unsafe_allow_html=True)


