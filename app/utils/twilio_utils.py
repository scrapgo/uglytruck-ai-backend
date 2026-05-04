def design_message_body(seller_data: dict):
    """
    Build the SMS body sent to the seller.

    Purpose: notify the seller that we've just sent them an email
    regarding their truck enquiry, and prompt them to check it.
    """
    message = (
        f"Hello {seller_data['FNAME']}, "
        f"We've just sent you an email regarding your enquiry for the "
        f"{seller_data['MAKE']} {seller_data.get('MODEL') or ''} "
        f"(Status: {seller_data['STATUS']}).\n\n"
        "Please check your inbox for full details and next steps. "
        "If you don't see it, please also check your spam or promotions folder.\n\n"
        "Thank you for partnering with UglyTruck.ai!"
    )
    return message