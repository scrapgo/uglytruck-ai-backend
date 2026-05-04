class Queries:
    """Reusable SQL queries for admin and truck operations"""

    # Database management
    CREATE_DATABASE = 'CREATE DATABASE "{db_name}"'
    UPDATE_DATABASE = 'ALTER DATABASE "{old_name}" RENAME TO "{new_name}"'
    LIST_TABLES = "SELECT tablename FROM pg_tables WHERE schemaname='public';"

    # Table management
    CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS "{table_name}" (
        date_created TIMESTAMP WITHOUT TIME ZONE PRIMARY KEY DEFAULT now(),
        date_modified TIMESTAMP WITHOUT TIME ZONE,
        last_modified_by VARCHAR,
        source VARCHAR,
        year INTEGER,
        make VARCHAR,
        model VARCHAR,
        vin VARCHAR,
        mileage INTEGER,
        engine VARCHAR,
        engine_serial_no VARCHAR,
        transmission VARCHAR,
        location_city VARCHAR,
        state VARCHAR,
        runs BOOLEAN,
        truck_condition_notes TEXT,
        title BOOLEAN,
        first_name VARCHAR,
        last_name VARCHAR,
        seller_phone VARCHAR,
        seller_email VARCHAR,
        seller_make VARCHAR,
        seller_target NUMERIC(12,2),
        buyer_offer NUMERIC(12,2),
        tow_price NUMERIC(12,2),
        offer_price NUMERIC(12,2),
        price_spread NUMERIC(12,2),
        status_old VARCHAR,
        deal_notes TEXT,
        buyer_notes TEXT,
        close_date DATE,
        photos TEXT,
        photo_exterior_driver_side TEXT,
        photo_engine_driver_side TEXT,
        photo_cab TEXT,
        photo_engine_passenger_side TEXT,
        photo_exterior_front TEXT,
        photo_exterior_passenger_side TEXT,
        photo_exterior_rear TEXT,
        copy_photo_exterior_driver_side TEXT,
        scrapgo_logo TEXT,
        print_pdf TEXT,
        add_note TEXT,
        num_of_notes INTEGER,
        maximum_date_modified TIMESTAMP WITHOUT TIME ZONE,
        user_name VARCHAR,
        status VARCHAR(100),
        fedex_label_sent BOOLEAN,
        pick_up_address TEXT,
        pickup_contact VARCHAR(100),
        pickup_phone VARCHAR(50),
        alternative_contact VARCHAR(100),
        alternative_phone VARCHAR(50),
        buyer_bill_of_sale_google_sheet TEXT,
        buyer_bill_of_sale_sheet_cb TEXT,
        alternate_contact_pipeline VARCHAR(100),
        alternate_phone_pipeline VARCHAR(50),
        updated_form BOOLEAN
    );
    """

    UNIQUE_KEY_CONSTRAINT = 'ALTER TABLE "{table_name}" ADD CONSTRAINT uniq_vin_date UNIQUE (vin, date_created);'
    DELETE_TABLE = 'DROP TABLE IF EXISTS "{table_name}" CASCADE;'
    ADD_COLUMN = 'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS {column_name} {column_type};'
    DROP_COLUMN = 'ALTER TABLE "{table_name}" DROP COLUMN IF EXISTS {column_name} CASCADE;'
    FLUSH_TABLE = 'TRUNCATE TABLE "{table_name}" RESTART IDENTITY CASCADE;'

    # Fetch all rows from any table
    FETCH_TABLE = 'SELECT {column_part} FROM "{table_name}"{where_clause} ORDER BY date_created DESC;'

    # Truck-related operations
    SELECT_ALL_TRUCKS = 'SELECT * FROM "{table_name}";'
    SELECT_LIMIT_BY_TRUCKS = 'SELECT * FROM "{table_name}" LIMIT {limit};'
    SELECT_TRUCK_BY_ID = 'SELECT * FROM "{table_name}" WHERE id="{truck_id}";'
    SELECT_TRUCK_BY_STATUS = 'SELECT * FROM "{table_name}" WHERE status="{status}";'

    # Update Records
    UPDATE_RECORD = '''
    UPDATE "{table_name}"
    SET {set_clause} = $1
    WHERE {where_key} = $2
    RETURNING *;
    '''
    # Insert Records
    INSERT_RECORD = '''
            INSERT INTO "{table_name}" (
                record_id, date_created, date_modified, last_modified_by, source, year, make, model, vin, mileage, engine,
                engine_serial_no, transmission, location_city, state, runs, truck_condition_notes, title, first_name, last_name,
                seller_phone, seller_email, seller_make, seller_target, buyer_offer, tow_price, offer_price, price_spread,
                status_old, deal_notes, buyer_notes, close_date, photos, photo_exterior_driver_side, photo_engine_driver_side,
                photo_cab, photo_engine_passenger_side, photo_exterior_front, photo_exterior_passenger_side, photo_exterior_rear,
                copy_photo_exterior_driver_side, scrapgo_logo, print_pdf, add_note, num_of_notes, maximum_date_modified,
                user_name, status, fedex_label_sent, pick_up_address, pickup_contact, pickup_phone, alternative_contact,
                alternative_phone, buyer_bill_of_sale_google_sheet, buyer_bill_of_sale_sheet_cb, alternate_contact_pipeline,
                alternate_phone_pipeline, updated_form
            )
            VALUES (
    $1,  $2,  $3,  $4,  $5,  $6,  $7,  $8,  $9,  $10,
    $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
    $21, $22, $23, $24, $25, $26, $27, $28, $29, $30,
    $31, $32, $33, $34, $35, $36, $37, $38, $39, $40,
    $41, $42, $43, $44, $45, $46, $47, $48, $49, $50,
    $51, $52, $53, $54, $55, $56, $57, $58, $59
);
        '''

    INSERT_RECORD_PSYCOP = '''
    INSERT INTO "{table_name}" (
        record_id, date_created, date_modified, last_modified_by, source, year, make, model,
        vin, mileage, engine, engine_serial_no, transmission, location_city, state, runs,
        truck_condition_notes, title, first_name, last_name, seller_phone, seller_email, seller_make,
        seller_target, buyer_offer, tow_price, offer_price, price_spread, status_old,
        deal_notes, buyer_notes, close_date, photos, photo_exterior_driver_side,
        photo_engine_driver_side, photo_cab, photo_engine_passenger_side,
        photo_exterior_front, photo_exterior_passenger_side, photo_exterior_rear,
        copy_photo_exterior_driver_side, scrapgo_logo, print_pdf, add_note,
        num_of_notes, maximum_date_modified, user_name, status, fedex_label_sent,
        pick_up_address, pickup_contact, pickup_phone, alternative_contact,
        alternative_phone, buyer_bill_of_sale_google_sheet, buyer_bill_of_sale_sheet_cb,
        alternate_contact_pipeline, alternate_phone_pipeline, updated_form
    )
    VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s
    );
'''