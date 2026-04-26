"""
swagger_config.py — OpenAPI/Swagger specification for GarageFlow

Registers with Flasgger to serve interactive API docs at /docs.
"""

SWAGGER_TEMPLATE = {
    "swagger": "2.0",
    "info": {
        "title": "GarageFlow API",
        "description": "Parking garage management system — real-time occupancy, reservations, payments, and analytics.",
        "version": "1.0.0",
    },
    "basePath": "/",
    "schemes": ["http", "https"],
    "securityDefinitions": {
        "Bearer": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Enter: Bearer <token>",
        }
    },
    "tags": [
        {"name": "Auth", "description": "Authentication and session management"},
        {"name": "Garage", "description": "Garage configuration"},
        {"name": "Capacity", "description": "Occupancy and capacity monitoring"},
        {"name": "Floors", "description": "Floor management"},
        {"name": "Spaces", "description": "Parking space management"},
        {"name": "Tickets", "description": "Vehicle entry/exit lifecycle"},
        {"name": "Reservations", "description": "Reservation CRUD and check-in"},
        {"name": "Payments", "description": "Stripe payment processing"},
        {"name": "Analytics", "description": "Utilization, occupancy, peak hours, revenue"},
        {"name": "Gates", "description": "Gate management and overrides"},
        {"name": "Pricing", "description": "Pricing rule management"},
        {"name": "Staff", "description": "Staff account management"},
        {"name": "Admin", "description": "Admin dashboard and audit log"},
        {"name": "Webhooks", "description": "Stripe webhook handler"},
    ],
    "paths": {
        # ── Auth ──────────────────────────────────────────────
        "/v1/auth/login": {
            "post": {
                "tags": ["Auth"],
                "summary": "Log in with username and password",
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                    }
                }}],
                "responses": {
                    "200": {"description": "Token and user profile"},
                    "401": {"description": "Invalid credentials"},
                    "429": {"description": "Rate limited"},
                },
            }
        },
        "/v1/auth/refresh": {
            "post": {
                "tags": ["Auth"],
                "summary": "Refresh Bearer token",
                "security": [{"Bearer": []}],
                "responses": {
                    "200": {"description": "New token"},
                    "401": {"description": "Invalid or expired token"},
                },
            }
        },
        "/v1/auth/logout": {
            "post": {
                "tags": ["Auth"],
                "summary": "Revoke current token",
                "security": [{"Bearer": []}],
                "responses": {"200": {"description": "Token revoked"}},
            }
        },
        "/v1/auth/me": {
            "get": {
                "tags": ["Auth"],
                "summary": "Get current user profile",
                "security": [{"Bearer": []}],
                "responses": {
                    "200": {"description": "User profile"},
                    "401": {"description": "Not authenticated"},
                },
            }
        },
        "/v1/auth/register": {
            "post": {
                "tags": ["Auth"],
                "summary": "Register new staff account (admin only)",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["username", "password", "name"],
                    "properties": {
                        "username": {"type": "string"},
                        "password": {"type": "string", "minLength": 8},
                        "name": {"type": "string"},
                        "role": {"type": "string", "enum": ["admin", "attendant"], "default": "attendant"},
                    }
                }}],
                "responses": {
                    "201": {"description": "Account created with token"},
                    "400": {"description": "Validation error"},
                    "409": {"description": "Username taken"},
                },
            }
        },
        "/v1/auth/change-password": {
            "post": {
                "tags": ["Auth"],
                "summary": "Change own password",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["currentPassword", "newPassword"],
                    "properties": {
                        "currentPassword": {"type": "string"},
                        "newPassword": {"type": "string", "minLength": 8},
                    }
                }}],
                "responses": {"200": {"description": "Password changed"}},
            }
        },

        # ── Garage ────────────────────────────────────────────
        "/v1/garage": {
            "get": {
                "tags": ["Garage"],
                "summary": "Get garage configuration",
                "responses": {
                    "200": {"description": "Garage details"},
                    "404": {"description": "No garage configured"},
                },
            },
            "post": {
                "tags": ["Garage"],
                "summary": "Create a new garage (admin)",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["name", "totalCapacity", "numberOfFloors", "operatingHours"],
                    "properties": {
                        "name": {"type": "string"},
                        "totalCapacity": {"type": "integer"},
                        "numberOfFloors": {"type": "integer"},
                        "operatingHours": {"type": "string"},
                        "frontDeskPhone": {"type": "string"},
                    }
                }}],
                "responses": {"201": {"description": "Garage created"}},
            },
        },
        "/v1/garage/{garage_id}": {
            "put": {
                "tags": ["Garage"],
                "summary": "Update garage configuration (admin)",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "path", "name": "garage_id", "type": "integer", "required": True},
                    {"in": "body", "name": "body", "schema": {"type": "object", "properties": {
                        "name": {"type": "string"},
                        "totalCapacity": {"type": "integer"},
                        "numberOfFloors": {"type": "integer"},
                        "operatingHours": {"type": "string"},
                        "frontDeskPhone": {"type": "string"},
                    }}},
                ],
                "responses": {"200": {"description": "Garage updated"}},
            },
            "delete": {
                "tags": ["Garage"],
                "summary": "Delete garage (admin, cascades)",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "path", "name": "garage_id", "type": "integer", "required": True}],
                "responses": {"200": {"description": "Garage deleted"}},
            },
        },

        # ── Capacity ──────────────────────────────────────────
        "/v1/capacity": {
            "get": {
                "tags": ["Capacity"],
                "summary": "Get total/occupied/available spot counts by type",
                "parameters": [{"in": "query", "name": "garage_id", "type": "integer", "required": False}],
                "responses": {"200": {"description": "Capacity breakdown"}},
            }
        },
        "/v1/capacity/status": {
            "get": {
                "tags": ["Capacity"],
                "summary": "Get available spot counts by type",
                "parameters": [{"in": "query", "name": "garage_id", "type": "integer", "required": False}],
                "responses": {"200": {"description": "Available counts map"}},
            }
        },
        "/v1/capacity/floors/{floor_id}": {
            "get": {
                "tags": ["Capacity"],
                "summary": "Get capacity for a single floor",
                "parameters": [{"in": "path", "name": "floor_id", "type": "integer", "required": True}],
                "responses": {"200": {"description": "Floor capacity"}},
            }
        },
        "/v1/capacity/alert": {
            "get": {
                "tags": ["Capacity"],
                "summary": "Get congestion alert status",
                "parameters": [{"in": "query", "name": "garage_id", "type": "integer", "required": False}],
                "responses": {"200": {"description": "Alert state with occupancy rate"}},
            }
        },

        # ── Tickets ───────────────────────────────────────────
        "/v1/tickets": {
            "post": {
                "tags": ["Tickets"],
                "summary": "Vehicle entry — assign spot, create ticket",
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["licensePlate", "driverClass"],
                    "properties": {
                        "licensePlate": {"type": "string"},
                        "driverClass": {"type": "string", "enum": ["standard", "accessibility", "employee", "eco"]},
                        "phone": {"type": "string"},
                        "plateState": {"type": "string"},
                    }
                }}],
                "responses": {
                    "201": {"description": "Ticket created"},
                    "409": {"description": "Duplicate active ticket"},
                    "503": {"description": "Garage full"},
                },
            },
            "get": {
                "tags": ["Tickets"],
                "summary": "List tickets (default: active)",
                "parameters": [
                    {"in": "query", "name": "status", "type": "string", "required": False},
                    {"in": "query", "name": "plate", "type": "string", "required": False},
                    {"in": "query", "name": "phone", "type": "string", "required": False},
                ],
                "responses": {"200": {"description": "Array of tickets"}},
            },
        },
        "/v1/tickets/{ticket_id}": {
            "get": {
                "tags": ["Tickets"],
                "summary": "Get single ticket",
                "parameters": [{"in": "path", "name": "ticket_id", "type": "integer", "required": True}],
                "responses": {"200": {"description": "Ticket details"}},
            },
            "delete": {
                "tags": ["Tickets"],
                "summary": "Delete ticket (admin)",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "path", "name": "ticket_id", "type": "integer", "required": True}],
                "responses": {"204": {"description": "Ticket deleted"}},
            },
        },
        "/v1/tickets/{ticket_id}/exit": {
            "put": {
                "tags": ["Tickets"],
                "summary": "Vehicle exit — calculate fee, release spot",
                "parameters": [
                    {"in": "path", "name": "ticket_id", "type": "integer", "required": True},
                    {"in": "body", "name": "body", "schema": {
                        "type": "object",
                        "required": ["licensePlate", "paymentMethod"],
                        "properties": {
                            "licensePlate": {"type": "string"},
                            "paymentMethod": {"type": "string", "enum": ["cash", "card", "mobile"]},
                        }
                    }},
                ],
                "responses": {
                    "200": {"description": "Exit processed with fee"},
                    "409": {"description": "Ticket already closed or plate mismatch"},
                },
            }
        },
        "/v1/tickets/{ticket_id}/personal": {
            "delete": {
                "tags": ["Tickets"],
                "summary": "Wipe PII from ticket",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "path", "name": "ticket_id", "type": "integer", "required": True}],
                "responses": {"204": {"description": "PII wiped"}},
            }
        },
        "/v1/tickets/{ticket_id}/override": {
            "post": {
                "tags": ["Tickets"],
                "summary": "Force-close, void, or correct spot (admin)",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "path", "name": "ticket_id", "type": "integer", "required": True},
                    {"in": "body", "name": "body", "schema": {
                        "type": "object",
                        "required": ["action"],
                        "properties": {
                            "action": {"type": "string", "enum": ["force_close", "void", "correct_spot"]},
                            "reason": {"type": "string"},
                            "spotId": {"type": "integer"},
                        }
                    }},
                ],
                "responses": {"200": {"description": "Override applied"}},
            }
        },

        # ── Reservations ──────────────────────────────────────
        "/v1/reservations": {
            "post": {
                "tags": ["Reservations"],
                "summary": "Create reservation",
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["phone", "scheduledArrival"],
                    "properties": {
                        "phone": {"type": "string"},
                        "scheduledArrival": {"type": "string", "format": "date-time"},
                        "driverClass": {"type": "string"},
                        "licensePlate": {"type": "string"},
                        "vehicleId": {"type": "integer"},
                        "endDatetime": {"type": "string", "format": "date-time"},
                        "quotedFee": {"type": "number"},
                    }
                }}],
                "responses": {
                    "201": {"description": "Reservation created"},
                    "503": {"description": "Garage full"},
                },
            },
            "get": {
                "tags": ["Reservations"],
                "summary": "List reservations",
                "parameters": [
                    {"in": "query", "name": "plate", "type": "string", "required": False},
                    {"in": "query", "name": "phone", "type": "string", "required": False},
                    {"in": "query", "name": "includeOld", "type": "boolean", "required": False},
                ],
                "responses": {"200": {"description": "Array of reservations"}},
            },
        },
        "/v1/reservations/{reservation_id}": {
            "get": {
                "tags": ["Reservations"],
                "summary": "Get single reservation",
                "parameters": [{"in": "path", "name": "reservation_id", "type": "string", "required": True}],
                "responses": {"200": {"description": "Reservation details"}},
            },
            "put": {
                "tags": ["Reservations"],
                "summary": "Update reservation",
                "parameters": [
                    {"in": "path", "name": "reservation_id", "type": "string", "required": True},
                    {"in": "body", "name": "body", "schema": {"type": "object", "properties": {
                        "scheduledArrival": {"type": "string", "format": "date-time"},
                        "endDatetime": {"type": "string", "format": "date-time"},
                        "status": {"type": "string"},
                        "quotedFee": {"type": "number"},
                    }}},
                ],
                "responses": {"200": {"description": "Reservation updated"}},
            },
            "delete": {
                "tags": ["Reservations"],
                "summary": "Cancel reservation (requires identity verification)",
                "parameters": [
                    {"in": "path", "name": "reservation_id", "type": "string", "required": True},
                    {"in": "body", "name": "body", "schema": {
                        "type": "object",
                        "required": ["licensePlate", "phone"],
                        "properties": {
                            "licensePlate": {"type": "string"},
                            "phone": {"type": "string"},
                        }
                    }},
                ],
                "responses": {"200": {"description": "Reservation cancelled"}},
            },
        },
        "/v1/reservations/{reservation_id}/check": {
            "put": {
                "tags": ["Reservations"],
                "summary": "Check in reservation — creates active ticket",
                "parameters": [
                    {"in": "path", "name": "reservation_id", "type": "string", "required": True},
                    {"in": "body", "name": "body", "schema": {
                        "type": "object",
                        "required": ["licensePlate"],
                        "properties": {"licensePlate": {"type": "string"}}
                    }},
                ],
                "responses": {"201": {"description": "Ticket created from reservation"}},
            }
        },

        # ── Payments ──────────────────────────────────────────
        "/v1/payments/config": {
            "get": {
                "tags": ["Payments"],
                "summary": "Get Stripe publishable key",
                "responses": {"200": {"description": "Stripe config"}},
            }
        },
        "/v1/payments/create-intent": {
            "post": {
                "tags": ["Payments"],
                "summary": "Create Stripe PaymentIntent",
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["ticketId"],
                    "properties": {"ticketId": {"type": "integer"}}
                }}],
                "responses": {"200": {"description": "Client secret and amount"}},
            }
        },
        "/v1/payments": {
            "post": {
                "tags": ["Payments"],
                "summary": "Charge a closed ticket via Stripe",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["ticketId", "paymentIntentId"],
                    "properties": {
                        "ticketId": {"type": "integer"},
                        "paymentIntentId": {"type": "string"},
                    }
                }}],
                "responses": {"201": {"description": "Payment recorded"}},
            },
            "get": {
                "tags": ["Payments"],
                "summary": "Query payments by ticket or plate",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "query", "name": "ticketId", "type": "integer", "required": False},
                    {"in": "query", "name": "plate", "type": "string", "required": False},
                ],
                "responses": {"200": {"description": "Payment details"}},
            },
        },
        "/v1/payments/{payment_id}": {
            "get": {
                "tags": ["Payments"],
                "summary": "Get payment details",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "path", "name": "payment_id", "type": "integer", "required": True}],
                "responses": {"200": {"description": "Payment details"}},
            },
        },
        "/v1/payments/{payment_id}/refund": {
            "post": {
                "tags": ["Payments"],
                "summary": "Process refund via Stripe",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "path", "name": "payment_id", "type": "integer", "required": True},
                    {"in": "body", "name": "body", "schema": {"type": "object", "properties": {
                        "amount": {"type": "number", "description": "Partial refund amount (omit for full)"},
                    }}},
                ],
                "responses": {"200": {"description": "Refund processed"}},
            }
        },
        "/v1/payments/{payment_id}/override": {
            "post": {
                "tags": ["Payments"],
                "summary": "Admin manual payment override",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "path", "name": "payment_id", "type": "integer", "required": True},
                    {"in": "body", "name": "body", "schema": {"type": "object", "properties": {
                        "amountCharged": {"type": "number"},
                        "paymentStatus": {"type": "string"},
                        "paymentMethod": {"type": "string"},
                    }}},
                ],
                "responses": {"200": {"description": "Payment overridden"}},
            }
        },
        "/v1/payments/reports": {
            "get": {
                "tags": ["Payments"],
                "summary": "Revenue summary over date range",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "query", "name": "start", "type": "string", "format": "date-time", "required": True},
                    {"in": "query", "name": "end", "type": "string", "format": "date-time", "required": True},
                    {"in": "query", "name": "garage_id", "type": "integer", "required": False},
                ],
                "responses": {"200": {"description": "Revenue report"}},
            }
        },

        # ── Analytics ─────────────────────────────────────────
        "/v1/analytics/utilization": {
            "get": {
                "tags": ["Analytics"],
                "summary": "Utilization rate over time",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "query", "name": "start", "type": "string", "format": "date-time", "required": True},
                    {"in": "query", "name": "end", "type": "string", "format": "date-time", "required": True},
                    {"in": "query", "name": "floor_id", "type": "integer", "required": False},
                    {"in": "query", "name": "garage_id", "type": "integer", "required": False},
                ],
                "responses": {"200": {"description": "Utilization buckets"}},
            }
        },
        "/v1/analytics/occupancy": {
            "get": {
                "tags": ["Analytics"],
                "summary": "Live occupancy and 30-day trend",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "query", "name": "floor_id", "type": "integer", "required": False},
                    {"in": "query", "name": "garage_id", "type": "integer", "required": False},
                ],
                "responses": {"200": {"description": "Live counts and trend data"}},
            }
        },
        "/v1/analytics/peak-hours": {
            "get": {
                "tags": ["Analytics"],
                "summary": "Peak usage by hour of day",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "query", "name": "start", "type": "string", "format": "date-time", "required": True},
                    {"in": "query", "name": "end", "type": "string", "format": "date-time", "required": True},
                    {"in": "query", "name": "garage_id", "type": "integer", "required": False},
                ],
                "responses": {"200": {"description": "Hourly peak data"}},
            }
        },
        "/v1/analytics/revenue": {
            "get": {
                "tags": ["Analytics"],
                "summary": "Revenue totals and session stats",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "query", "name": "from", "type": "string", "format": "date-time", "required": False},
                    {"in": "query", "name": "to", "type": "string", "format": "date-time", "required": False},
                    {"in": "query", "name": "garage_id", "type": "integer", "required": False},
                ],
                "responses": {"200": {"description": "Revenue report"}},
            }
        },

        # ── Floors ────────────────────────────────────────────
        "/v1/floors": {
            "get": {
                "tags": ["Floors"],
                "summary": "List all floors",
                "parameters": [{"in": "query", "name": "garage_id", "type": "integer", "required": False}],
                "responses": {"200": {"description": "Array of floors"}},
            },
            "post": {
                "tags": ["Floors"],
                "summary": "Create floor (admin)",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["garageId", "floorNumber", "totalSpots"],
                    "properties": {
                        "garageId": {"type": "integer"},
                        "floorNumber": {"type": "integer"},
                        "totalSpots": {"type": "integer"},
                        "floorName": {"type": "string"},
                    }
                }}],
                "responses": {"201": {"description": "Floor created"}},
            },
        },

        # ── Spaces ────────────────────────────────────────────
        "/v1/spaces": {
            "get": {
                "tags": ["Spaces"],
                "summary": "List all parking spaces",
                "parameters": [{"in": "query", "name": "garage_id", "type": "integer", "required": False}],
                "responses": {"200": {"description": "Array of spaces"}},
            }
        },
        "/v1/spaces/available": {
            "get": {
                "tags": ["Spaces"],
                "summary": "List available spaces",
                "parameters": [
                    {"in": "query", "name": "type", "type": "string", "required": False},
                    {"in": "query", "name": "garage_id", "type": "integer", "required": False},
                ],
                "responses": {"200": {"description": "Array of available spaces"}},
            }
        },

        # ── Gates ─────────────────────────────────────────────
        "/v1/gates": {
            "get": {
                "tags": ["Gates"],
                "summary": "List all gates",
                "parameters": [{"in": "query", "name": "garage_id", "type": "integer", "required": False}],
                "responses": {"200": {"description": "Array of gates"}},
            },
            "post": {
                "tags": ["Gates"],
                "summary": "Create gate (admin)",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["garageId", "gateType"],
                    "properties": {
                        "garageId": {"type": "integer"},
                        "gateType": {"type": "string", "enum": ["entry", "exit"]},
                    }
                }}],
                "responses": {"201": {"description": "Gate created"}},
            },
        },

        # ── Pricing ───────────────────────────────────────────
        "/v1/pricing": {
            "get": {
                "tags": ["Pricing"],
                "summary": "List all pricing rules",
                "responses": {"200": {"description": "Array of pricing rules"}},
            },
            "post": {
                "tags": ["Pricing"],
                "summary": "Create pricing rule (admin)",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["rateName", "applicableHours", "pricingModel", "description", "program"],
                    "properties": {
                        "rateName": {"type": "string"},
                        "applicableHours": {"type": "string"},
                        "pricingModel": {"type": "string", "enum": ["flat", "hourly", "special"]},
                        "description": {"type": "string"},
                        "program": {"type": "string", "enum": ["flat", "hourly", "special"]},
                    }
                }}],
                "responses": {"201": {"description": "Rule created"}},
            },
        },

        # ── Staff ─────────────────────────────────────────────
        "/v1/staff": {
            "post": {
                "tags": ["Staff"],
                "summary": "Create staff account (admin)",
                "security": [{"Bearer": []}],
                "parameters": [{"in": "body", "name": "body", "schema": {
                    "type": "object",
                    "required": ["name", "username", "password"],
                    "properties": {
                        "name": {"type": "string"},
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                        "role": {"type": "string", "enum": ["admin", "attendant"]},
                    }
                }}],
                "responses": {"201": {"description": "Staff created"}},
            }
        },

        # ── Admin ─────────────────────────────────────────────
        "/v1/users": {
            "get": {
                "tags": ["Admin"],
                "summary": "List all staff accounts (admin)",
                "security": [{"Bearer": []}],
                "responses": {"200": {"description": "Array of users"}},
            }
        },
        "/v1/admin/history": {
            "get": {
                "tags": ["Admin"],
                "summary": "Query audit log with filters and pagination",
                "security": [{"Bearer": []}],
                "parameters": [
                    {"in": "query", "name": "userId", "type": "integer", "required": False},
                    {"in": "query", "name": "action", "type": "string", "required": False},
                    {"in": "query", "name": "from", "type": "string", "format": "date-time", "required": False},
                    {"in": "query", "name": "to", "type": "string", "format": "date-time", "required": False},
                    {"in": "query", "name": "page", "type": "integer", "required": False, "default": 1},
                    {"in": "query", "name": "limit", "type": "integer", "required": False, "default": 50},
                ],
                "responses": {"200": {"description": "Paginated audit events"}},
            }
        },

        # ── Webhooks ──────────────────────────────────────────
        "/v1/webhooks/stripe": {
            "post": {
                "tags": ["Webhooks"],
                "summary": "Handle Stripe webhook events",
                "parameters": [{"in": "body", "name": "payload", "schema": {"type": "object"}}],
                "responses": {"200": {"description": "Event processed"}},
            }
        },

        # ── Health ────────────────────────────────────────────
        "/health": {
            "get": {
                "tags": ["System"],
                "summary": "Health check",
                "responses": {"200": {"description": "{status: ok}"}},
            }
        },
    },
}

SWAGGER_CONFIG = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/apispec.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/docs",
}
