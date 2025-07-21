# AI Task Breakdown Agent - Enhanced with Smart Scheduling
# Requirements: pip install langchain langchain-community flask google-api-python-client google-auth-httplib2 google-auth-oauthlib python-dateutil

from flask import Flask, render_template, request, jsonify
from langchain_community.llms import Ollama
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import json
import os
import re
from dateutil import parser as date_parser
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Google Calendar setup
SCOPES = ['https://www.googleapis.com/auth/calendar']

class TaskBreakdownAgent:
    def __init__(self, model_name="llama3.2:latest"):
        # Initialize Ollama LLM
        self.llm = Ollama(model=model_name, temperature=0.5)
        
        # Initialize Google Calendar service
        self.calendar_service = None
        self._setup_google_calendar()
    
    def _setup_google_calendar(self):
        """Setup Google Calendar API authentication"""
        creds = None
        
        # Check if token.json exists
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        
        # If there are no valid credentials, request authorization
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if os.path.exists('credentials.json'):
                    flow = InstalledAppFlow.from_client_secrets_file(
                        'credentials.json', SCOPES)
                    creds = flow.run_local_server(port=0)
                else:
                    print("Warning: credentials.json not found. Google Calendar integration disabled.")
                    return
            
            # Save the credentials for the next run
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
        
        try:
            self.calendar_service = build('calendar', 'v3', credentials=creds)
            print("Google Calendar service initialized successfully")
        except Exception as e:
            print(f"Failed to initialize Google Calendar service: {e}")
    
    def extract_deadline_from_task(self, task: str) -> Tuple[Optional[datetime], str]:
        """Extract deadline/date information from the task description"""
        # Common date patterns
        date_patterns = [
            r'by (\w+ \d{1,2}(?:st|nd|rd|th)?(?:, \d{4})?)',  # by March 15th, by March 15th, 2024
            r'due (\w+ \d{1,2}(?:st|nd|rd|th)?(?:, \d{4})?)',  # due March 15th
            r'before (\w+ \d{1,2}(?:st|nd|rd|th)?(?:, \d{4})?)',  # before March 15th
            r'on (\w+ \d{1,2}(?:st|nd|rd|th)?(?:, \d{4})?)',  # on March 15th
            r'(\d{1,2}/\d{1,2}/\d{4})',  # 03/15/2024
            r'(\d{1,2}-\d{1,2}-\d{4})',  # 03-15-2024
            r'(\w+ \d{1,2}(?:st|nd|rd|th)?)',  # March 15th
            r'(next \w+)',  # next Monday, next week
            r'(this \w+)',  # this Friday
            r'(tomorrow)',  # tomorrow
            r'(today)',     # today
        ]
        
        # Time patterns
        time_patterns = [
            r'at (\d{1,2}:\d{2}(?:\s*(?:AM|PM|am|pm))?)',  # at 2:30 PM
            r'by (\d{1,2}:\d{2}(?:\s*(?:AM|PM|am|pm))?)',  # by 2:30 PM
            r'(\d{1,2}(?:\s*(?:AM|PM|am|pm)))',  # 2 PM
        ]
        
        deadline = None
        cleaned_task = task
        
        # Look for date patterns
        for pattern in date_patterns:
            match = re.search(pattern, task, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                try:
                    # Parse the date
                    parsed_date = date_parser.parse(date_str, fuzzy=True)
                    
                    # If no year specified, assume current year
                    if parsed_date.year == 1900:
                        parsed_date = parsed_date.replace(year=datetime.now().year)
                    
                    # If the date is in the past, assume next year
                    if parsed_date.date() < datetime.now().date():
                        parsed_date = parsed_date.replace(year=datetime.now().year + 1)
                    
                    deadline = parsed_date
                    
                    # Look for time patterns in the same text
                    for time_pattern in time_patterns:
                        time_match = re.search(time_pattern, task, re.IGNORECASE)
                        if time_match:
                            time_str = time_match.group(1)
                            try:
                                time_parsed = date_parser.parse(time_str, fuzzy=True)
                                deadline = deadline.replace(
                                    hour=time_parsed.hour,
                                    minute=time_parsed.minute
                                )
                                break
                            except:
                                pass
                    
                    # Remove the date/time text from the task
                    cleaned_task = re.sub(pattern, '', task, flags=re.IGNORECASE).strip()
                    for time_pattern in time_patterns:
                        cleaned_task = re.sub(time_pattern, '', cleaned_task, flags=re.IGNORECASE).strip()
                    
                    break
                except Exception as e:
                    print(f"Error parsing date '{date_str}': {e}")
                    continue
        
        return deadline, cleaned_task
    
    def get_calendar_availability(self, start_date: datetime, days_ahead: int = 14) -> List[Dict[str, datetime]]:
        """Get available time slots from calendar"""
        if not self.calendar_service:
            return []
        
        try:
            # Get events for the next 'days_ahead' days
            end_date = start_date + timedelta(days=days_ahead)
            
            events_result = self.calendar_service.events().list(
                calendarId='primary',
                timeMin=start_date.isoformat() + 'Z',
                timeMax=end_date.isoformat() + 'Z',
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            
            # Convert events to busy periods
            busy_periods = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                end = event['end'].get('dateTime', event['end'].get('date'))
                
                if start and end:
                    try:
                        start_dt = date_parser.parse(start)
                        end_dt = date_parser.parse(end)
                        busy_periods.append({'start': start_dt, 'end': end_dt})
                    except:
                        continue
            
            # Find available slots (working hours: 9 AM - 6 PM, Monday-Friday)
            available_slots = []
            current_date = start_date.replace(hour=9, minute=0, second=0, microsecond=0)
            
            while current_date.date() <= end_date.date():
                # Skip weekends
                if current_date.weekday() >= 5:
                    current_date += timedelta(days=1)
                    current_date = current_date.replace(hour=9, minute=0, second=0, microsecond=0)
                    continue
                
                # Check each hour slot during working hours
                work_start = current_date.replace(hour=9, minute=0)
                work_end = current_date.replace(hour=18, minute=0)
                
                slot_start = work_start
                while slot_start < work_end:
                    slot_end = slot_start + timedelta(hours=1)
                    
                    # Check if this slot conflicts with any busy period
                    is_available = True
                    for busy in busy_periods:
                        if (slot_start < busy['end'] and slot_end > busy['start']):
                            is_available = False
                            break
                    
                    if is_available:
                        available_slots.append({
                            'start': slot_start,
                            'end': slot_end
                        })
                    
                    slot_start += timedelta(hours=1)
                
                current_date += timedelta(days=1)
                current_date = current_date.replace(hour=9, minute=0, second=0, microsecond=0)
            
            return available_slots[:20]  # Return first 20 available slots
            
        except Exception as e:
            print(f"Error getting calendar availability: {e}")
            return []
    
    def find_optimal_start_time(self, deadline: Optional[datetime], total_duration_minutes: int) -> datetime:
        """Find the optimal start time based on deadline and calendar availability"""
        now = datetime.now()
        
        # If no deadline specified, start looking from next business day
        if deadline is None:
            search_start = now + timedelta(days=1)
            search_start = search_start.replace(hour=9, minute=0, second=0, microsecond=0)
            # Skip to next Monday if it's weekend
            while search_start.weekday() >= 5:
                search_start += timedelta(days=1)
        else:
            # Work backwards from deadline
            search_start = now + timedelta(hours=1)
            search_start = search_start.replace(minute=0, second=0, microsecond=0)
        
        # Get available slots
        available_slots = self.get_calendar_availability(search_start)
        
        if not available_slots:
            # Fallback: schedule for next business day at 9 AM
            fallback = now + timedelta(days=1)
            fallback = fallback.replace(hour=9, minute=0, second=0, microsecond=0)
            while fallback.weekday() >= 5:
                fallback += timedelta(days=1)
            return fallback
        
        # If deadline specified, find slots that allow completion before deadline
        if deadline:
            total_duration_hours = total_duration_minutes / 60
            for slot in available_slots:
                potential_end = slot['start'] + timedelta(hours=total_duration_hours)
                if potential_end <= deadline:
                    return slot['start']
            
            # If no slot found before deadline, use earliest available slot
            print(f"Warning: Cannot complete all tasks before deadline {deadline}")
        
        # Return the earliest available slot
        return available_slots[0]['start']
    
    def break_down_task(self, task: str) -> Dict[str, Any]:
        """Break down a task using the LLM"""
        try:
            # Extract deadline from task
            deadline, cleaned_task = self.extract_deadline_from_task(task)
            
            # Create the prompt
            prompt = f"""You are an expert project manager and task breakdown specialist. 
            Your job is to analyze a given task and break it down into smaller, actionable subtasks.

            Consider the following when breaking down tasks:
            1. Each subtask should be specific and actionable
            2. Estimate realistic time durations for each subtask (minimum 30 minutes)
            3. Identify dependencies between subtasks
            4. Assign appropriate priority levels (high, medium, low)
            5. Ensure subtasks are in logical order
            6. EXCLUDE any subtasks that take less than 30 minutes - only include substantial work

            Task to break down: {cleaned_task}

            Return your response as a JSON object with this exact structure:
            {{
                "main_task": "the original task",
                "subtasks": [
                    {{
                        "title": "brief title for subtask",
                        "description": "detailed description", 
                        "estimated_duration": 60,
                        "priority": "high",
                        "dependencies": []
                    }}
                ],
                "total_estimated_time": 240
            }}

            IMPORTANT: Only include subtasks that require 30 minutes or more. Skip quick tasks like "send email" or "make phone call" unless they involve substantial preparation or complex coordination.

            Provide ONLY the JSON response, no additional text or markdown formatting."""
            
            # Get response from LLM
            response = self.llm.invoke(prompt)
            
            # Parse the JSON response
            parsed_result = self._parse_llm_response(response)
            
            # Filter out tasks less than 30 minutes
            filtered_subtasks = []
            for subtask in parsed_result.get("subtasks", []):
                if subtask.get("estimated_duration", 0) >= 30:
                    filtered_subtasks.append(subtask)
                else:
                    print(f"Skipping short task: {subtask.get('title', 'Unknown')} ({subtask.get('estimated_duration', 0)} min)")
            
            # Update the result
            parsed_result["subtasks"] = filtered_subtasks
            parsed_result["total_estimated_time"] = sum(
                subtask.get("estimated_duration", 0) for subtask in filtered_subtasks
            )
            
            # Add deadline information
            parsed_result["deadline"] = deadline.isoformat() if deadline else None
            parsed_result["has_deadline"] = deadline is not None
            
            return parsed_result
            
        except Exception as e:
            print(f"Error in task breakdown: {e}")
            # Fallback response
            return {
                "main_task": task,
                "subtasks": [
                    {
                        "title": "Analyze task requirements",
                        "description": "Break down and understand what needs to be done",
                        "estimated_duration": 60,
                        "priority": "high",
                        "dependencies": []
                    }
                ],
                "total_estimated_time": 60,
                "deadline": None,
                "has_deadline": False
            }
    
    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """Parse the LLM response as JSON"""
        try:
            # Clean the response text
            cleaned_response = response.strip()
            
            # Remove markdown formatting if present
            if cleaned_response.startswith('```json'):
                cleaned_response = cleaned_response[7:]
            if cleaned_response.startswith('```'):
                cleaned_response = cleaned_response[3:]
            if cleaned_response.endswith('```'):
                cleaned_response = cleaned_response[:-3]
            
            cleaned_response = cleaned_response.strip()
            
            # Parse JSON
            result = json.loads(cleaned_response)
            
            # Validate and ensure required fields exist
            if not isinstance(result, dict):
                raise ValueError("Response is not a dictionary")
            
            if "main_task" not in result:
                result["main_task"] = "Unknown task"
            
            if "subtasks" not in result or not isinstance(result["subtasks"], list):
                result["subtasks"] = []
            
            # Calculate total time if not provided
            if "total_estimated_time" not in result:
                total_time = sum(subtask.get("estimated_duration", 0) for subtask in result["subtasks"])
                result["total_estimated_time"] = total_time
            
            # Ensure each subtask has required fields
            for subtask in result["subtasks"]:
                if "title" not in subtask:
                    subtask["title"] = "Untitled task"
                if "description" not in subtask:
                    subtask["description"] = "No description provided"
                if "estimated_duration" not in subtask:
                    subtask["estimated_duration"] = 60
                if "priority" not in subtask:
                    subtask["priority"] = "medium"
                if "dependencies" not in subtask:
                    subtask["dependencies"] = []
            
            return result
            
        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON: {e}")
            print(f"Raw response: {response}")
            # Return a fallback structure
            return {
                "main_task": "Failed to parse task",
                "subtasks": [{
                    "title": "Manual task breakdown needed",
                    "description": "The AI response could not be parsed. Please try again with a simpler task description.",
                    "estimated_duration": 60,
                    "priority": "high",
                    "dependencies": []
                }],
                "total_estimated_time": 60
            }
        except Exception as e:
            print(f"Unexpected error parsing response: {e}")
            return {
                "main_task": "Error processing task",
                "subtasks": [{
                    "title": "Error occurred",
                    "description": f"An error occurred while processing: {str(e)}",
                    "estimated_duration": 30,
                    "priority": "high",
                    "dependencies": []
                }],
                "total_estimated_time": 30
            }
    
    def create_calendar_events(self, breakdown: Dict[str, Any], start_date: datetime = None) -> List[Dict[str, Any]]:
        """Create calendar events for each subtask with intelligent scheduling"""
        if not self.calendar_service:
            print("Google Calendar service not available")
            return []
        
        subtasks = breakdown.get("subtasks", [])
        if not subtasks:
            return []
        
        # Get deadline if specified
        deadline = None
        if breakdown.get("deadline"):
            try:
                deadline = date_parser.parse(breakdown["deadline"])
            except:
                deadline = None
        
        # Find optimal start time
        total_duration = breakdown.get("total_estimated_time", 0)
        if start_date is None:
            start_date = self.find_optimal_start_time(deadline, total_duration)
        
        created_events = []
        current_time = start_date
        
        print(f"Scheduling {len(subtasks)} tasks starting from {start_date}")
        if deadline:
            print(f"Target completion: {deadline}")
        
        for i, subtask in enumerate(subtasks):
            duration_minutes = subtask.get('estimated_duration', 60)
            
            # Skip if less than 30 minutes (should already be filtered, but double-check)
            if duration_minutes < 30:
                print(f"Skipping short task: {subtask.get('title', 'Unknown')} ({duration_minutes} min)")
                continue
            
            # Create event
            event_title = subtask.get("title", f"Task {i+1}")
            event_description = f"{subtask.get('description', 'No description')}\n\nPriority: {subtask.get('priority', 'medium')}\nEstimated Duration: {duration_minutes} minutes"
            
            if deadline:
                event_description += f"\nProject Deadline: {deadline.strftime('%Y-%m-%d %H:%M')}"
            
            event = {
                'summary': event_title,
                'description': event_description,
                'start': {
                    'dateTime': current_time.isoformat(),
                    'timeZone': 'America/Los_Angeles',
                },
                'end': {
                    'dateTime': (current_time + timedelta(minutes=duration_minutes)).isoformat(),
                    'timeZone': 'America/Los_Angeles',
                },
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'email', 'minutes': 60},  # 1 hour before
                        {'method': 'popup', 'minutes': 15},  # 15 minutes before
                    ],
                },
            }
            
            try:
                created_event = self.calendar_service.events().insert(calendarId='primary', body=event).execute()
                created_events.append({
                    'title': event_title,
                    'event_id': created_event['id'],
                    'link': created_event.get('htmlLink'),
                    'start_time': current_time.strftime('%Y-%m-%d %H:%M'),
                    'end_time': (current_time + timedelta(minutes=duration_minutes)).strftime('%Y-%m-%d %H:%M'),
                    'duration': duration_minutes,
                    'priority': subtask.get('priority', 'medium')
                })
                
                print(f"Created event: {event_title} at {current_time}")
                
                # Schedule next task with buffer time
                buffer_minutes = 30  # 30-minute buffer between tasks
                next_start = current_time + timedelta(minutes=duration_minutes + buffer_minutes)
                
                # If next start would be outside working hours, move to next working day
                if next_start.hour >= 18 or next_start.hour < 9:
                    next_start = next_start.replace(hour=9, minute=0, second=0, microsecond=0)
                    if next_start.hour >= 18:  # If it's after work hours
                        next_start += timedelta(days=1)
                    
                    # Skip weekends
                    while next_start.weekday() >= 5:
                        next_start += timedelta(days=1)
                
                current_time = next_start
                
            except Exception as e:
                print(f"Error creating calendar event for {event_title}: {e}")
                created_events.append({
                    'title': event_title,
                    'error': str(e),
                    'start_time': current_time.strftime('%Y-%m-%d %H:%M'),
                    'duration': duration_minutes
                })
                
                # Still advance time even if event creation failed
                current_time += timedelta(minutes=duration_minutes + 30)
        
        return created_events

# Flask Web Application
app = Flask(__name__)
agent = TaskBreakdownAgent()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/process-task', methods=['POST'])
def process_task():
    try:
        data = request.json
        task = data.get('task', '').strip()
        
        if not task:
            return jsonify({'error': 'Task is required'}), 400
        
        # Break down the task
        breakdown = agent.break_down_task(task)
        
        # Create calendar events
        calendar_events = agent.create_calendar_events(breakdown)
        
        # Prepare response
        response = {
            'main_task': breakdown['main_task'],
            'subtasks': breakdown['subtasks'],
            'total_estimated_time': breakdown['total_estimated_time'],
            'calendar_events': calendar_events
        }
        
        return jsonify(response)
        
    except Exception as e:
        print(f"Error processing task: {e}")
        return jsonify({'error': 'Failed to process task'}), 500

if __name__ == '__main__':
    print("Starting AI Task Breakdown Agent...")
    print("Make sure Ollama is running with: ollama serve")
    print("Make sure you have a model pulled: ollama pull llama3.2:latest")
    print("Navigate to: http://localhost:5000")
    app.run(debug=True, port=5000)
