"""
Sign Detection Module - Real-time Hand Gesture Tracking with MediaPipe
"""
# Configure OpenCV for headless/container environments before import
import os
os.environ['OPENCV_VIDEOIO_DEBUG'] = '0'
os.environ['OPENCV_LOG_LEVEL'] = 'OFF'

try:
    import cv2
    # Set numpy-based loading to avoid GUI libraries
    if hasattr(cv2, 'ocl'):
        cv2.ocl.setUseOpenCL(False)
except ImportError as e:
    cv2 = None
except Exception as e:
    # Catch any other cv2 initialization errors (graphics libs, etc.)
    print(f"⚠️  Warning: OpenCV not fully available ({e}) - sign detection may be limited")
    cv2 = None

try:
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions
    from mediapipe.python import Image, ImageFormat
    MEDIAPIPE_AVAILABLE = True
except ImportError as e:
    MEDIAPIPE_AVAILABLE = False
    vision = None
    print(f"⚠️  MediaPipe import error: {e}")
except Exception as e:
    MEDIAPIPE_AVAILABLE = False
    vision = None
    print(f"⚠️  MediaPipe initialization error: {e}")

import numpy as np
from typing import Dict, List, Tuple, Optional, Generator
import time
import base64
from io import BytesIO
from PIL import Image as PILImage
import os

class SignDetector:
    """
    Sign Language Detector using MediaPipe Hand Tracking
    Real-time hand landmark detection and gesture recognition
    """
    
    def __init__(self, model_path: str = None, confidence_threshold: float = 0.5):
        """
        Initialize sign detector with MediaPipe Hand Landmarker
        
        Args:
            model_path: Optional path to custom model
            confidence_threshold: Minimum confidence for hand detection (0.0-1.0)
        """
        self.model_path = model_path
        # Store original threshold for gesture filtering (not MediaPipe detection threshold)
        self.confidence_threshold = confidence_threshold
        self.hand_detector = None
        self.is_model_loaded = False
        
        if not MEDIAPIPE_AVAILABLE:
            print("⚠️  MediaPipe not available - sign detection will be disabled")
            return
        
        try:
            # Get path to hand landmarker model
            model_file = self._get_hand_model_path()
            
            # Initialize MediaPipe Hand Landmarker
            # Keep MediaPipe detection threshold LOW (0.3) to catch all hands,
            # then filter results in gesture classification logic
            base_options = BaseOptions(model_asset_path=model_file)
            options = vision.HandLandmarkerOptions(
                base_options=base_options,
                running_mode=vision.RunningMode.IMAGE,
                num_hands=2,
                min_hand_detection_confidence=0.3,  # LOW threshold - let MediaPipe be permissive
                min_hand_presence_confidence=0.5
            )
            self.hand_detector = vision.HandLandmarker.create_from_options(options)
            self.is_model_loaded = True
            print(f"✓ Sign detector initialized successfully. Model loaded: {self.is_model_loaded}")
        except Exception as e:
            print(f"⚠️  Error initializing sign detector: {e}")
            import traceback
            traceback.print_exc()
            self.hand_detector = None
            self.is_model_loaded = False
        
        self.last_gesture = None
        self.gesture_history = []
        self.gesture_smoothing_frames = 3
    
    def _get_hand_model_path(self) -> str:
        """Get path to hand landmarker model file"""
        # First try the local models directory
        local_model = os.path.join(
            os.path.dirname(__file__),
            'models',
            'hand_landmarker.task'
        )
        if os.path.exists(local_model):
            return local_model
        
        # Fallback to home directory
        home_model = os.path.expanduser('~/.mediapipe/hand_landmarker.task')
        if os.path.exists(home_model):
            return home_model
        
        # If no model found, raise error
        raise FileNotFoundError(
            "Hand landmarker model not found. "
            "Please download it using: mkdir -p models && "
            "curl -L 'https://storage.googleapis.com/mediapipe-assets/hand_landmarker.task' "
            "-o models/hand_landmarker.task"
        )
        
    def frame_to_cv2(self, frame_data) -> Optional[np.ndarray]:
        """
        Convert various frame formats to OpenCV format
        
        Args:
            frame_data: Frame as bytes, PIL Image, or numpy array
            
        Returns:
            OpenCV format frame (RGB as numpy array) or None
        """
        try:
            if isinstance(frame_data, bytes):
                pil_image = PILImage.open(BytesIO(frame_data))
                # Ensure image is in RGB format (convert from BGR, RGBA, etc.)
                if pil_image.mode != 'RGB':
                    pil_image = pil_image.convert('RGB')
                # Convert PIL Image to numpy array (RGB format)
                frame_array = np.array(pil_image)
                if frame_array.shape[2] != 3 or frame_array.shape[0] == 0 or frame_array.shape[1] == 0:
                    print(f"⚠️  Invalid frame shape after PIL conversion: {frame_array.shape}")
                    return None
                return frame_array
            elif isinstance(frame_data, PILImage.Image):
                # Ensure PIL Image is in RGB format
                if frame_data.mode != 'RGB':
                    frame_data = frame_data.convert('RGB')
                return np.array(frame_data)
            elif isinstance(frame_data, np.ndarray):
                if len(frame_data.shape) == 3 and frame_data.shape[2] == 3:
                    # Validate frame is not corrupted (all black usually indicates frame error)
                    if np.mean(frame_data) > 1:  # At least some pixels should be non-zero
                        return frame_data
                    else:
                        print(f"⚠️  Frame appears to be invalid (all black/zero): {np.mean(frame_data)}")
                        return None
            return None
        except Exception as e:
            print(f"Error converting frame: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def detect_hand_landmarks(self, frame) -> Dict:
        """
        Detect hand landmarks in a video frame using MediaPipe
        
        Args:
            frame: Video frame from webcam (numpy array, PIL Image, or bytes)
            
        Returns:
            Dict with hand detection results
        """
        result = {
            'hands_detected': 0,
            'hand_landmarks': [],
            'hand_positions': [],
            'gestures': [],
            'confidence': 0.0,
            'has_hands': False,
            'hand_keypoints': [],
            'debug_info': {}  # For troubleshooting
        }
        
        if not self.hand_detector:
            result['debug_info']['error'] = 'Hand detector not initialized'
            print(f"⚠️  Hand detector is None. Model loaded: {self.is_model_loaded}")
            return result
        
        try:
            # Convert frame if necessary
            cv_frame = frame if isinstance(frame, np.ndarray) else self.frame_to_cv2(frame)
            if cv_frame is None:
                result['debug_info']['error'] = 'Frame conversion failed or frame is None'
                return result
            
            result['debug_info']['frame_shape'] = cv_frame.shape
            result['debug_info']['frame_dtype'] = str(cv_frame.dtype)
            
            # frame_to_cv2 already returns RGB format, use directly
            frame_rgb = cv_frame
            
            # Create MediaPipe Image
            mp_image = Image(image_format=ImageFormat.SRGB, data=frame_rgb)
            
            # Run hand detection
            hand_results = self.hand_detector.detect(mp_image)
            
            if hand_results.hand_landmarks:
                result['hands_detected'] = len(hand_results.hand_landmarks)
                result['has_hands'] = True
                
                h, w, c = cv_frame.shape
                avg_confidence = 0
                
                for i, hand_landmarks in enumerate(hand_results.hand_landmarks):
                    # Extract landmarks
                    landmarks = []
                    keypoints = []
                    
                    for landmark in hand_landmarks:
                        landmarks.append({
                            'x': landmark.x,
                            'y': landmark.y,
                            'z': landmark.z,
                            'visibility': getattr(landmark, 'visibility', 1.0)
                        })
                        keypoints.append([landmark.x * w, landmark.y * h])
                    
                    result['hand_landmarks'].append(landmarks)
                    result['hand_keypoints'].append(keypoints)
                    
                    # Get handedness (left/right)
                    if hand_results.handedness and i < len(hand_results.handedness):
                        handedness = hand_results.handedness[i][0]  # Get first classification
                        result['hand_positions'].append({
                            'label': handedness.category_name,
                            'confidence': handedness.score
                        })
                        avg_confidence += handedness.score
                    else:
                        result['hand_positions'].append({
                            'label': 'Unknown',
                            'confidence': 0.0
                        })
                    
                    # Classify gesture
                    gesture = self._classify_gesture(landmarks)
                    result['gestures'].append(gesture)
                
                if result['hand_positions']:
                    result['confidence'] = avg_confidence / len(result['hand_positions'])
            
        except Exception as e:
            print(f"Error detecting hand landmarks: {e}")
            import traceback
            traceback.print_exc()
            result['debug_info']['exception'] = str(e)
        
        return result
    
    def detect_signs(self, frame) -> Dict:
        """
        Detect sign language gestures in a video frame
        Only returns results when hands are actually detected
        
        Args:
            frame: Video frame from webcam
            
        Returns:
            Dict with detected signs, hands, and confidence
        """
        hand_detection = self.detect_hand_landmarks(frame)
        
        result = {
            'detected_sign': None,
            'confidence': hand_detection['confidence'],
            'landmarks': hand_detection['hand_landmarks'],
            'hand_positions': hand_detection['hand_positions'],
            'gestures': hand_detection['gestures'],
            'hands_detected': hand_detection['hands_detected'],
            'has_hands': hand_detection['has_hands'],
            'timestamp': time.time()
        }
        
        # Only transcribe if hands are actually detected
        if hand_detection['has_hands'] and hand_detection['gestures']:
            # Use the first detected gesture
            result['detected_sign'] = hand_detection['gestures'][0]
        
        return result
    
    def _classify_gesture(self, landmarks: List[Dict]) -> str:
        """
        Classify ASL hand gesture from landmarks
        Recognizes common American Sign Language signs
        
        Args:
            landmarks: List of 21 hand landmarks with x, y, z coordinates
            
        Returns:
            ASL sign name
        """
        if not landmarks or len(landmarks) < 21:
            return 'unknown'
        
        try:
            # Extract hand shape and position features
            hand_shape = self._get_hand_shape(landmarks)
            hand_position = self._get_hand_position(landmarks)
            palm_facing = self._get_palm_orientation(landmarks)
            hand_movement = self._get_hand_movement_pattern(landmarks)
            
            # Match to ASL signs based on feature combination
            asl_sign = self._match_asl_sign(
                hand_shape=hand_shape,
                position=hand_position,
                palm_facing=palm_facing,
                movement=hand_movement,
                landmarks=landmarks
            )
            
            return asl_sign
            
        except Exception as e:
            print(f"Error classifying gesture: {e}")
            return 'unknown'
    
    def _get_hand_shape(self, landmarks: List[Dict]) -> Dict:
        """
        Determine hand shape based on finger positions
        Improved accuracy with better thresholds and multi-point analysis
        Returns dict with finger states
        """
        shape = {
            'index_up': False,
            'middle_up': False,
            'ring_up': False,
            'pinky_up': False,
            'thumb_extended': False,
            'fingers_spread': False,
            'fingers_touching': False,
            'closed_fist': False
        }
        
        try:
            # Improved finger detection with multi-point analysis
            # Each finger: tip at index*4+4, PIP at index*4+2
            shape['index_up'] = self._is_finger_up(landmarks, 1)
            shape['middle_up'] = self._is_finger_up(landmarks, 2)
            shape['ring_up'] = self._is_finger_up(landmarks, 3)
            shape['pinky_up'] = self._is_finger_up(landmarks, 4)
            
            # Check thumb with improved detection
            shape['thumb_extended'] = self._is_thumb_extended(landmarks)
            
            # Check if fingers are spread (distance between finger tips)
            if shape['index_up'] or shape['middle_up'] or shape['ring_up'] or shape['pinky_up']:
                index_pos = landmarks[8]  # Index tip
                middle_pos = landmarks[12]  # Middle tip
                ring_pos = landmarks[16]  # Ring tip
                pinky_pos = landmarks[20]  # Pinky tip
                
                spread_dist = [
                    abs(index_pos['x'] - middle_pos['x']),
                    abs(middle_pos['x'] - ring_pos['x']),
                    abs(ring_pos['x'] - pinky_pos['x'])
                ]
                # Improved threshold for spread detection
                avg_spread = sum(spread_dist) / len(spread_dist)
                shape['fingers_spread'] = avg_spread > 0.12
            
            # Check for closed fist - all fingers should be down
            fingers_down = sum([
                not shape['index_up'],
                not shape['middle_up'],
                not shape['ring_up'],
                not shape['pinky_up']
            ])
            shape['closed_fist'] = (fingers_down == 4 and not shape['thumb_extended'])
            
            # Check if fingers are touching (very close together)
            if shape['index_up'] or shape['middle_up'] or shape['ring_up'] or shape['pinky_up']:
                spread_sum = sum(spread_dist) if len(spread_dist) >= 3 else 0
                shape['fingers_touching'] = spread_sum < 0.05
            
        except Exception as e:
            print(f"Error getting hand shape: {e}")
        
        return shape
    
    def _get_hand_position(self, landmarks: List[Dict]) -> Dict:
        """
        Determine hand position relative to face and body
        Improved accuracy with better thresholds and focus area detection
        """
        position = {
            'height': 'middle',  # top, middle, bottom
            'side': 'center',    # left, center, right
            'distance': 'normal' # close, normal, far
        }
        
        try:
            palm_base = landmarks[0]  # Wrist
            
            # Improved height detection with better thresholds
            if palm_base['y'] < 0.3:
                position['height'] = 'top'
            elif palm_base['y'] > 0.7:
                position['height'] = 'bottom'
            else:
                position['height'] = 'middle'
            
            # Improved side detection with better thresholds
            if palm_base['x'] < 0.35:
                position['side'] = 'left'
            elif palm_base['x'] > 0.65:
                position['side'] = 'right'
            else:
                position['side'] = 'center'
            
            # Improved distance detection using multiple points
            # Combine wrist and finger tip positions for better z-depth
            finger_tips = [landmarks[4], landmarks[8], landmarks[12], landmarks[16], landmarks[20]]
            avg_z = (palm_base['z'] + sum(t['z'] for t in finger_tips)) / (len(finger_tips) + 1)
            
            # More refined thresholds for depth
            if avg_z < -0.05:
                position['distance'] = 'far'
            elif avg_z > 0.15:
                position['distance'] = 'close'
            else:
                position['distance'] = 'normal'
            
            # Additional: detect if hand is near face/body (specific ASL context)
            # Hand near top-center is typically face location
            if position['height'] == 'top' and position['side'] == 'center':
                position['focus_area'] = 'face'
            elif position['height'] == 'bottom' and position['side'] == 'center':
                position['focus_area'] = 'chest'
            elif position['height'] == 'middle' and position['side'] == 'center':
                position['focus_area'] = 'neutral'
            else:
                position['focus_area'] = 'side_space'
                
        except Exception as e:
            print(f"Error getting hand position: {e}")
        
        return position
    
    def _get_palm_orientation(self, landmarks: List[Dict]) -> str:
        """
        Determine which direction palm is facing
        """
        try:
            # Use hand landmarks to determine palm normal direction
            palm_base = landmarks[0]
            middle_finger_mcp = landmarks[9]
            
            # Vector from palm to middle finger
            vector_x = middle_finger_mcp['x'] - palm_base['x']
            vector_y = middle_finger_mcp['y'] - palm_base['y']
            vector_z = middle_finger_mcp['z'] - palm_base['z']
            
            # Determine primary orientation
            if abs(vector_z) > abs(vector_x) and abs(vector_z) > abs(vector_y):
                if vector_z > 0:
                    return 'away'  # Palm facing away
                else:
                    return 'toward'  # Palm facing toward
            elif abs(vector_y) > abs(vector_x):
                if vector_y > 0:
                    return 'down'
                else:
                    return 'up'
            else:
                if vector_x > 0:
                    return 'right'
                else:
                    return 'left'
                    
        except Exception as e:
            print(f"Error getting palm orientation: {e}")
            return 'unknown'
    
    def _get_hand_movement_pattern(self, landmarks: List[Dict]) -> str:
        """
        Analyze hand movement pattern
        This would ideally track landmarks over multiple frames
        """
        # This is simplified for single frame; could be enhanced with frame history
        return 'static'
    
    def _match_asl_sign(self, hand_shape: Dict, position: Dict, 
                       palm_facing: str, movement: str, landmarks: List[Dict]) -> str:
        """
        Match detected hand pose to ASL sign with improved accuracy
        Based on hand shape, position, and orientation
        Checks custom trained gestures first, then built-in ASL signs
        Uses more specific conditions to reduce false positives
        """
        
        # First, try to match against custom trained gestures
        custom_match = self.recognize_custom_gesture(
            hand_shape, position, palm_facing, landmarks
        )
        if custom_match:
            return custom_match.upper()
        
        fingers_up = sum([
            hand_shape['index_up'],
            hand_shape['middle_up'],
            hand_shape['ring_up'],
            hand_shape['pinky_up']
        ])
        
        # ASL SIGN CLASSIFICATION LOGIC - IMPROVED ACCURACY
        # ==================================================
        # IMPORTANT: Order matters! Earlier conditions can shadow later ones.
        # More specific conditions with additional constraints should come before generic ones.
        
        # HELLO/WAVE - Open hand at side/top, fingers spread, with motion context
        # More specific: high hand at edge with palm away, NOT close to face
        if (fingers_up == 5 and hand_shape['fingers_spread'] and 
            position['side'] in ['right', 'left'] and 
            position['height'] in ['top', 'middle'] and
            palm_facing in ['away', 'down'] and
            position['distance'] in ['normal', 'far'] and
            position.get('focus_area') != 'face'):  # Not at face = waving motion
            return 'HELLO'
        
        # THANK YOU - Open hand, palm up/toward, near mouth/chin
        # Specific: high position, palm facing up, close distance
        if (fingers_up == 5 and position['height'] in ['top', 'middle'] and
            palm_facing in ['up', 'toward'] and position['distance'] == 'close' and
            hand_shape['fingers_spread']):
            return 'THANK_YOU'
        
        # LIKE/LOVE - Thumb and middle finger, other fingers down
        # Specific: exactly middle + thumb up
        if (hand_shape['thumb_extended'] and hand_shape['middle_up'] and
            not hand_shape['index_up'] and not hand_shape['ring_up'] and
            not hand_shape['pinky_up'] and position['distance'] == 'close' and
            position['height'] == 'middle'):
            return 'LIKE'
        
        # YES - Fist with thumb up, middle height
        # Specific: only thumb extended, closed fist
        if (hand_shape['closed_fist'] and hand_shape['thumb_extended'] and
            position['height'] in ['middle', 'bottom'] and
            not hand_shape['index_up']):
            return 'YES'
        
        # NO - Index and middle finger pointing up together
        # Very specific: exactly index + middle only
        if (hand_shape['index_up'] and hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up'] and
            not hand_shape['thumb_extended'] and
            position['side'] in ['center', 'left']):
            return 'NO'
        
        # STOP - Open hand, palm facing front/away, middle height
        # Specific: all fingers up, spread, palm away
        if (fingers_up == 5 and hand_shape['fingers_spread'] and
            palm_facing in ['away', 'down'] and position['height'] == 'middle' and
            position['distance'] in ['normal', 'far']):
            return 'STOP'
        
        # PLEASE - Open hand, palm toward, at chest
        # Specific: all fingers, palm toward or down, close + chest
        if (fingers_up == 5 and position['distance'] == 'close' and
            position['height'] in ['middle', 'bottom'] and 
            palm_facing in ['toward', 'down'] and
            hand_shape['fingers_spread']):
            return 'PLEASE'
        
        # SORRY - Fist at chest with rubbing motion
        # Specific: closed fist, close distance, middle/top height
        if (hand_shape['closed_fist'] and not hand_shape['thumb_extended'] and
            position['distance'] == 'close' and
            position['height'] in ['middle', 'top']):
            return 'SORRY'
        
        # HELP - Open hand(s), supporting motion
        # Specific: all fingers up, open hand, mid height
        if (fingers_up == 5 and not hand_shape['closed_fist'] and
            hand_shape['fingers_spread'] and
            position['height'] in ['middle', 'bottom']):
            return 'HELP'
        
        # GOOD - Open hand at chin, moving downward
        # Specific: all fingers, palm down/away, high position, close
        if (fingers_up == 5 and position['height'] in ['top', 'middle'] and
            palm_facing in ['away', 'down'] and position['distance'] == 'close' and
            hand_shape['fingers_spread']):
            return 'GOOD'
        
        # BAD - Open hand at chin, palm toward/up
        # Specific: all fingers, palm toward, high position
        if (fingers_up == 5 and position['height'] in ['top', 'middle'] and
            palm_facing in ['toward', 'up'] and position['distance'] == 'close' and
            hand_shape['fingers_spread']):
            return 'BAD'
        
        # SIGN - Peace/victory hand moving in arc
        # Specific: only index + middle up
        if (hand_shape['index_up'] and hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up'] and
            position['side'] in ['center', 'left'] and
            not hand_shape['closed_fist']):
            return 'SIGN'
        
        # UNDERSTAND/KNOW - Index at temple/forehead
        # Specific: only index up, very close, high
        if (hand_shape['index_up'] and not hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up'] and
            position['height'] == 'top' and position['distance'] == 'close'):
            return 'UNDERSTAND'
        
        # THINK - Index pointing at head
        # Specific: index only, close, high, not toward face
        if (hand_shape['index_up'] and not hand_shape['middle_up'] and
            position['height'] == 'top' and position['distance'] == 'close' and
            palm_facing != 'toward'):
            return 'THINK'
        
        # YOU - Index finger pointing outward, away
        # Specific: only index, pointing away from body
        if (hand_shape['index_up'] and not hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up'] and
            position['side'] in ['center', 'right'] and
            palm_facing in ['away', 'right'] and
            position['distance'] in ['normal', 'far']):
            return 'YOU'
        
        # ME/I - Index pointing to self at chest
        # Specific: index only, very close, lower position
        if (hand_shape['index_up'] and not hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up'] and
            position['distance'] == 'close' and position['height'] in ['middle', 'bottom']):
            return 'ME'
        
        # WHAT/QUESTION - Index and thumb apart
        # Specific: index + thumb only, middle height
        if (hand_shape['index_up'] and hand_shape['thumb_extended'] and
            not hand_shape['middle_up'] and not hand_shape['ring_up'] and
            not hand_shape['pinky_up'] and position['height'] == 'middle'):
            return 'WHAT'
        
        # WHERE - Index pointing with specific orientation
        # Specific: index only, middle/top, palm away
        if (hand_shape['index_up'] and not hand_shape['middle_up'] and
            position['height'] in ['middle', 'top'] and
            palm_facing == 'away' and position['distance'] in ['normal', 'far']):
            return 'WHERE'
        
        # GO - Open hand(s) moving forward/away
        # Specific: all fingers, palm away/forward, not close
        if (fingers_up == 5 and hand_shape['fingers_spread'] and
            palm_facing in ['away'] and position['distance'] in ['normal', 'far']):
            return 'GO'
        
        # COME - Open hand with inward-facing palm
        # Specific: all fingers, palm toward/down, pulling motion implied
        if (fingers_up == 5 and palm_facing in ['toward', 'down'] and
            hand_shape['fingers_spread'] and position['distance'] in ['normal', 'close']):
            return 'COME'
        
        # HAPPY - Smile tracing or thumbs up style
        # Specific: index only at top, close distance
        if (hand_shape['index_up'] and not hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up'] and
            position['height'] == 'top' and position['distance'] == 'close' and
            not hand_shape['thumb_extended']):
            return 'HAPPY'
        
        # NEW SIGNS - Extended recognition
        
        # ROCK - Index and pinky only (horn gesture)
        if (hand_shape['index_up'] and hand_shape['pinky_up'] and
            not hand_shape['middle_up'] and not hand_shape['ring_up'] and
            not hand_shape['thumb_extended']):
            return 'ROCK'
        
        # CALL_ME - Pinky to ear (phone shape)
        if (hand_shape['thumb_extended'] and hand_shape['pinky_up'] and
            not hand_shape['index_up'] and not hand_shape['middle_up'] and
            not hand_shape['ring_up'] and
            position['distance'] == 'close' and position['height'] == 'top'):
            return 'CALL_ME'
        
        # LOVE_YOU - Index, pinky, and thumb extended
        if (hand_shape['index_up'] and hand_shape['pinky_up'] and
            hand_shape['thumb_extended'] and not hand_shape['middle_up'] and
            not hand_shape['ring_up']):
            return 'LOVE_YOU'
        
        # THUMBS_UP - Only thumb up
        if (hand_shape['thumb_extended'] and not hand_shape['index_up'] and
            not hand_shape['middle_up'] and not hand_shape['ring_up'] and
            not hand_shape['pinky_up']):
            return 'THUMBS_UP'
        
        # THUMBS_DOWN - Thumb down at middle/bottom
        if (hand_shape['thumb_extended'] and not hand_shape['index_up'] and
            not hand_shape['middle_up'] and not hand_shape['ring_up'] and
            not hand_shape['pinky_up'] and position['height'] in ['middle', 'bottom']):
            return 'THUMBS_DOWN'
        
        # OK - Thumb and index meeting, other fingers up
        if (hand_shape['thumb_extended'] and hand_shape['index_up'] and
            hand_shape['middle_up'] and hand_shape['ring_up'] and
            hand_shape['pinky_up'] and position['distance'] == 'normal'):
            return 'OK'
        
        # WATER - W-hand shape with movement (all fingers spread with specific hand at top)
        if (fingers_up == 5 and hand_shape['fingers_spread'] and
            position['height'] == 'top' and position['distance'] == 'close'):
            return 'WATER'
        
        # FIRE - Fingers wiggling up (two hands typically)
        if (fingers_up == 5 and hand_shape['fingers_spread'] and
            position['height'] in ['middle', 'top']):
            return 'FIRE'
        
        # FRIEND - Index fingers locked together
        if (hand_shape['index_up'] and not hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up'] and
            position['side'] == 'center'):
            return 'FRIEND'
        
        # FAMILY - F-hand + C-hand motion
        if (fingers_up == 5 and hand_shape['thumb_extended'] and
            position['height'] == 'middle'):
            return 'FAMILY'
        
        # MAN - Index and thumb forming L shape, close to forehead
        if (hand_shape['index_up'] and hand_shape['thumb_extended'] and
            not hand_shape['middle_up'] and not hand_shape['ring_up'] and
            not hand_shape['pinky_up'] and
            position['height'] == 'top' and position['distance'] == 'close'):
            return 'MAN'
        
        # WOMAN - Similar to MAN but at different position
        if (hand_shape['index_up'] and hand_shape['thumb_extended'] and
            position['height'] in ['middle', 'bottom'] and
            position['distance'] == 'close'):
            return 'WOMAN'
        
        # DOG - Snapping motion, index and middle
        if (hand_shape['index_up'] and hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up'] and
            position['side'] == 'right'):
            return 'DOG'
        
        # CAT - Two fingers at sides of head (index and thumb)
        if (hand_shape['thumb_extended'] and hand_shape['index_up'] and
            not hand_shape['middle_up'] and not hand_shape['ring_up'] and
            not hand_shape['pinky_up'] and
            position['height'] == 'top' and position['distance'] == 'close'):
            return 'CAT'
        
        # HOUSE - Roof shape (hands together forming triangle)
        if (fingers_up == 5 and hand_shape['fingers_spread'] and
            position['distance'] == 'normal' and
            position['height'] in ['middle', 'top']):
            return 'HOUSE'
        
        # SCHOOL - Two hands clapping motion
        if (hand_shape['fingers_spread'] and fingers_up == 5 and
            position['height'] == 'middle' and position['distance'] == 'normal'):
            return 'SCHOOL'
        
        # WORK - Fist hitting palm
        if (hand_shape['closed_fist'] and not hand_shape['thumb_extended'] and
            position['height'] == 'middle' and position['distance'] == 'normal'):
            return 'WORK'
        
        # PLAY - Pinky and thumb extended (Y-hand)
        if (hand_shape['thumb_extended'] and hand_shape['pinky_up'] and
            not hand_shape['index_up'] and not hand_shape['middle_up'] and
            not hand_shape['ring_up']):
            return 'PLAY'
        
        # SLEEP - Hand at side of face/cheek, open
        if (fingers_up == 5 and position['height'] == 'top' and
            position['distance'] == 'close' and
            position['side'] in ['left', 'right']):
            return 'SLEEP'
        
        # EAT - Hand at mouth with fingers together
        if (fingers_up == 5 and position['height'] == 'top' and
            position['distance'] == 'close' and hand_shape['fingers_touching']):
            return 'EAT'
        
        # DRINK - C-hand shape at mouth
        if (hand_shape['thumb_extended'] and position['height'] == 'top' and
            position['distance'] == 'close'):
            return 'DRINK'
        
        # COFFEE - C-hand holding imaginary cup
        if (hand_shape['thumb_extended'] and hand_shape['index_up'] and
            not hand_shape['middle_up'] and position['height'] == 'middle'):
            return 'COFFEE'
        
        # FOOD - Open hand at mouth
        if (fingers_up == 5 and hand_shape['fingers_spread'] and
            position['height'] == 'top' and position['distance'] == 'close'):
            return 'FOOD'
        
        # READ - Index and middle pointing at palm
        if (hand_shape['index_up'] and hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up'] and
            position['side'] == 'center'):
            return 'READ'
        
        # WRITE - Pinching motion, thumb and index
        if (hand_shape['thumb_extended'] and hand_shape['index_up'] and
            not hand_shape['middle_up']):
            return 'WRITE'
        
        # LISTEN - C-hand at ear
        if (position['height'] == 'top' and position['distance'] == 'close' and
            position['side'] in ['left', 'right']):
            return 'LISTEN'
        
        # DANCE - Hand moving with fingers spread
        if (fingers_up == 5 and hand_shape['fingers_spread'] and
            position['side'] in ['left', 'right']):
            return 'DANCE'
        
        # DIFFERENT - Index fingers crossed
        if (hand_shape['index_up'] and not hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up'] and
            position['side'] == 'center'):
            return 'DIFFERENT'
        
        # SAME - Index and middle fingers together, moving
        if (hand_shape['index_up'] and hand_shape['middle_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up']):
            return 'SAME'
        
        # MORNING - Open hand at shoulder area, low
        if (fingers_up == 5 and hand_shape['fingers_spread'] and
            position['height'] == 'bottom'):
            return 'MORNING'
        
        # NIGHT - Open hand moving down from top
        if (fingers_up == 5 and position['height'] == 'top'):
            return 'NIGHT'
        
        # TODAY - Hand at middle, index fingers
        if (hand_shape['index_up'] and hand_shape['middle_up'] and
            position['height'] == 'middle'):
            return 'TODAY'
        
        # TOMORROW - Index pointing forward/away
        if (hand_shape['index_up'] and not hand_shape['middle_up'] and
            palm_facing == 'away' and position['distance'] in ['normal', 'far']):
            return 'TOMORROW'
        
        # YESTERDAY - Index pointing backward
        if (hand_shape['index_up'] and not hand_shape['middle_up'] and
            position['side'] == 'left' and position['distance'] in ['normal', 'far']):
            return 'YESTERDAY'
        
        # NOW - Both hands with index and middle up
        if (hand_shape['index_up'] and hand_shape['middle_up'] and
            position['height'] == 'middle' and position['distance'] == 'close'):
            return 'NOW'
        
        # LATER - Thumb and index, L-hand shape
        if (hand_shape['thumb_extended'] and hand_shape['index_up'] and
            not hand_shape['middle_up']):
            return 'LATER'
        
        # FAST - Hand moving with tension, all fingers
        if (fingers_up == 5 and position['distance'] in ['normal', 'far']):
            return 'FAST'
        
        # SLOW - Open hand moving slowly (typically detected by motion)
        if (fingers_up == 5 and hand_shape['fingers_spread'] and
            position['distance'] in ['close', 'normal']):
            return 'SLOW'
        
        # FUNNY - Index and middle at nose (wrinkling motion)
        if (hand_shape['index_up'] and hand_shape['middle_up'] and
            not hand_shape['ring_up'] and position['height'] == 'top'):
            return 'FUNNY'
        
        # ANGRY - Fist at face, intense
        if (hand_shape['closed_fist'] and position['height'] == 'top' and
            position['distance'] == 'close'):
            return 'ANGRY'
        
        # SAD - Hands at face moving down
        if (fingers_up == 5 and position['height'] == 'top' and
            position['distance'] == 'close'):
            return 'SAD'
        
        # TIRED - Fists at eyes
        if (hand_shape['closed_fist'] and position['height'] == 'top'):
            return 'TIRED'
        
        # SICK - Middle finger at forehead/mouth
        if (hand_shape['middle_up'] and not hand_shape['index_up'] and
            not hand_shape['ring_up'] and not hand_shape['pinky_up']):
            return 'SICK'
        
        # BETTER - Open hand moving upward
        if (fingers_up == 5 and hand_shape['fingers_spread'] and
            position['distance'] == 'close'):
            return 'BETTER'
        
        # HAND_CLOSED - Just a fist, very specific
        if (hand_shape['closed_fist'] and not hand_shape['thumb_extended']):
            return 'FIST'
        
        # Fallback returns for unrecognized but consistent gestures
        if fingers_up == 0:
            return 'FIST'
        elif fingers_up == 1:
            if hand_shape['index_up']:
                return 'POINTING'
            elif hand_shape['thumb_extended']:
                return 'THUMBS_UP'
            else:
                return 'ONE_FINGER'
        elif fingers_up == 2:
            if hand_shape['index_up'] and hand_shape['middle_up']:
                return 'PEACE'
            elif hand_shape['index_up'] and hand_shape['ring_up']:
                return 'TWO_FINGERS'
            elif hand_shape['index_up'] and hand_shape['pinky_up']:
                return 'INDEX_PINKY'
            else:
                return 'TWO_FINGERS'
        elif fingers_up == 3:
            return 'THREE_FINGERS'
        elif fingers_up == 4:
            return 'FOUR_FINGERS'
        elif fingers_up == 5:
            return 'OPEN_HAND' if hand_shape['fingers_spread'] else 'HAND'
        
        # Final unknown fallback - only reached if logic fails
        return 'UNKNOWN'
    
    def _distance(self, point1: Dict, point2: Dict) -> float:
        """Calculate Euclidean distance between two points"""
        dx = point1['x'] - point2['x']
        dy = point1['y'] - point2['y']
        dz = point1['z'] - point2['z']
        return np.sqrt(dx*dx + dy*dy + dz*dz)
    
    def _is_finger_up(self, landmarks: List[Dict], finger_index: int) -> bool:
        """Check if a finger is extended up"""
        tip = landmarks[finger_index * 4 + 4]
        pip = landmarks[finger_index * 4 + 2]
        return tip['y'] < pip['y']
    
    def _count_fingers_up(self, landmarks: List[Dict]) -> int:
        """
        Count how many fingers are extended up
        Improved for ASL recognition
        """
        if not landmarks or len(landmarks) < 21:
            return 0
        
        count = 0
        try:
            # Check each finger (index, middle, ring, pinky)
            # Fingers 1-4, tips are at indices 8, 12, 16, 20
            for finger_idx in range(1, 5):
                if self._is_finger_up(landmarks, finger_idx):
                    count += 1
            
            # Check thumb separately - it has different orientation
            if self._is_thumb_extended(landmarks):
                count += 1
            
        except Exception as e:
            print(f"Error counting fingers up: {e}")
        
        return count
    
    def _is_thumb_extended(self, landmarks: List[Dict]) -> bool:
        """
        Check if thumb is extended
        Thumb is extended when tip is further from base than typical resting position
        Uses both distance and y-axis similarity to other fingers check
        """
        try:
            thumb_tip = landmarks[4]
            thumb_ip = landmarks[3]
            thumb_mcp = landmarks[2]
            palm = landmarks[0]
            
            # Thumb is extended if tip is further from palm than MCP by significant margin
            thumb_tip_dist = self._distance(thumb_tip, palm)
            thumb_mcp_dist = self._distance(thumb_mcp, palm)
            
            # Use higher threshold (0.08 instead of 0.02) to reduce false positives
            # Also check that tip-to-palm distance is absolute (> 0.1) to ensure true extension
            return (thumb_tip_dist > thumb_mcp_dist + 0.08) and (thumb_tip_dist > 0.1)
        except:
            return False
    
    def transcribe_signs(self, detections: List[Dict]) -> str:
        """
        Convert sequence of detected signs into readable text
        Only processes frames where hands were detected
        
        Args:
            detections: List of detected signs with confidences
            
        Returns:
            Transcribed text
        """
        # Filter only detections with hands
        hand_detections = [d for d in detections if d.get('hands_detected', 0) > 0]
        
        if not hand_detections:
            return "No hand gestures detected"
        
        # Filter by confidence threshold
        high_confidence = [d for d in hand_detections if d['confidence'] > self.confidence_threshold]
        
        if not high_confidence:
            return "Gestures detected but confidence too low"
        
        # Convert signs to text
        signs = []
        for detection in high_confidence:
            if detection.get('detected_sign'):
                signs.append(detection['detected_sign'])
        
        if not signs:
            return "No clear gestures detected"
        
        # Remove duplicates but maintain order
        unique_signs = []
        for sign in signs:
            if sign not in unique_signs:
                unique_signs.append(sign)
        
        return ' '.join(unique_signs)
    
    def get_hand_detection_status(self, frame) -> Dict:
        """
        Get real-time hand detection status for UI feedback
        
        Args:
            frame: Current video frame
            
        Returns:
            Dict with detection status and hand count
        """
        detection = self.detect_signs(frame)
        return {
            'hands_detected': detection['hands_detected'],
            'has_hands': detection['has_hands'],
            'gestures': detection['gestures'],
            'confidence': detection['confidence']
        }
    
    def train_gesture(self, gesture_name: str, landmarks: List[Dict], 
                     hand_shape: Dict = None, position: Dict = None) -> None:
        """
        Train the detector to recognize a new ASL gesture
        Stores the gesture pattern for recognition
        
        Args:
            gesture_name: Name of the ASL sign to train
            landmarks: Hand landmarks from training sample
            hand_shape: Pre-calculated hand shape features
            position: Pre-calculated hand position features
        """
        try:
            # Initialize training data storage if needed
            if not hasattr(self, 'custom_gestures'):
                self.custom_gestures = {}
            
            # Calculate features if not provided
            if hand_shape is None:
                hand_shape = self._get_hand_shape(landmarks)
            if position is None:
                position = self._get_hand_position(landmarks)
            
            palm_facing = self._get_palm_orientation(landmarks)
            
            # Store gesture template
            gesture_template = {
                'name': gesture_name,
                'hand_shape': hand_shape,
                'position': position,
                'palm_facing': palm_facing,
                'landmark_template': landmarks,
                'trained_at': time.time()
            }
            
            # Store multiple training samples to improve recognition
            if gesture_name not in self.custom_gestures:
                self.custom_gestures[gesture_name] = []
            
            self.custom_gestures[gesture_name].append(gesture_template)
            
            print(f"Trained gesture: {gesture_name} ({len(self.custom_gestures[gesture_name])} samples)")
            
        except Exception as e:
            print(f"Error training gesture: {e}")
    
    def recognize_custom_gesture(self, hand_shape: Dict, position: Dict, 
                                palm_facing: str, landmarks: List[Dict]) -> Optional[str]:
        """
        Recognize a custom trained gesture
        
        Args:
            hand_shape: Detected hand shape features
            position: Detected hand position
            palm_facing: Detected palm orientation
            landmarks: Hand landmarks
            
        Returns:
            Recognized gesture name if match found, None otherwise
        """
        if not hasattr(self, 'custom_gestures') or not self.custom_gestures:
            return None
        
        try:
            best_match = None
            best_score = 0.0
            threshold = 0.6  # Minimum confidence to recognize
            
            # Compare against all trained gestures
            for gesture_name, templates in self.custom_gestures.items():
                for template in templates:
                    # Calculate similarity to template
                    score = self._calculate_gesture_similarity(
                        hand_shape, template['hand_shape'],
                        position, template['position'],
                        palm_facing, template['palm_facing'],
                        landmarks, template['landmark_template']
                    )
                    
                    if score > best_score:
                        best_score = score
                        best_match = gesture_name
            
            # Return match if confidence is high enough
            if best_score > threshold:
                return best_match
            
            return None
            
        except Exception as e:
            print(f"Error recognizing custom gesture: {e}")
            return None
    
    def _calculate_gesture_similarity(self, shape1: Dict, shape2: Dict,
                                     pos1: Dict, pos2: Dict,
                                     palm1: str, palm2: str,
                                     lm1: List[Dict], lm2: List[Dict]) -> float:
        """
        Calculate similarity between two gestures
        Returns score between 0.0 and 1.0
        """
        score = 0.0
        weight_sum = 0
        
        try:
            # Compare hand shape (weight: 0.4)
            shape_match = sum(1 for key in shape1 if shape1[key] == shape2.get(key))
            shape_score = shape_match / len(shape1) if len(shape1) > 0 else 0
            score += shape_score * 0.4
            weight_sum += 0.4
            
            # Compare position (weight: 0.3)
            pos_match = 0
            if pos1.get('height') == pos2.get('height'):
                pos_match += 1
            if pos1.get('side') == pos2.get('side'):
                pos_match += 1
            pos_score = pos_match / 2.0
            score += pos_score * 0.3
            weight_sum += 0.3
            
            # Compare palm facing (weight: 0.2)
            palm_score = 1.0 if palm1 == palm2 else 0.5
            score += palm_score * 0.2
            weight_sum += 0.2
            
            # Compare landmark positions (weight: 0.1)
            if len(lm1) == len(lm2):
                landmark_diffs = []
                for i in range(len(lm1)):
                    dist = self._distance(lm1[i], lm2[i])
                    landmark_diffs.append(dist)
                
                avg_diff = np.mean(landmark_diffs)
                # If average difference is small (< 0.1), high similarity
                landmark_score = max(0, 1.0 - (avg_diff / 0.1))
                score += landmark_score * 0.1
                weight_sum += 0.1
            
            # Normalize score
            if weight_sum > 0:
                score = score / weight_sum
            
            return min(1.0, max(0.0, score))
            
        except Exception as e:
            print(f"Error calculating similarity: {e}")
            return 0.0
    
    def get_trained_gestures(self) -> List[str]:
        """
        Get list of trained custom gestures
        
        Returns:
            List of custom gesture names
        """
        if not hasattr(self, 'custom_gestures'):
            return []
        
        return list(self.custom_gestures.keys())
    
    def get_gesture_samples(self, gesture_name: str) -> int:
        """
        Get number of training samples for a gesture
        """
        if not hasattr(self, 'custom_gestures'):
            return 0
        
        return len(self.custom_gestures.get(gesture_name, []))
    
    def clear_gesture_training(self) -> None:
        """
        Clear all custom gesture training data
        """
        self.custom_gestures = {}
        print("Cleared all trained gestures")
    
    def draw_hand_landmarks(self, frame: np.ndarray, landmarks_list: List[List[Dict]], 
                           hand_positions: List[Dict] = None, 
                           detected_signs: List[str] = None) -> np.ndarray:
        """
        Draw hand landmarks, skeleton, and tracking lines on frame
        Shows finger positions and hand connections for accuracy visualization
        
        Args:
            frame: OpenCV image frame
            landmarks_list: List of hand landmarks (one per hand)
            hand_positions: List of hand position info (left/right)
            detected_signs: List of detected signs for labeling
            
        Returns:
            Frame with drawn landmarks and tracking lines
        """
        if not landmarks_list:
            return frame
        
        try:
            h, w, c = frame.shape
            
            # MediaPipe hand skeleton connections (21 joints)
            HAND_CONNECTIONS = [
                (0, 1), (1, 2), (2, 3), (3, 4),      # Thumb
                (0, 5), (5, 6), (6, 7), (7, 8),      # Index finger
                (0, 9), (9, 10), (10, 11), (11, 12), # Middle finger
                (0, 13), (13, 14), (14, 15), (15, 16),  # Ring finger
                (0, 17), (17, 18), (18, 19), (19, 20)   # Pinky
            ]
            
            # Colors for different hands
            colors = [(0, 255, 0), (255, 0, 0)]  # Green for right, Red for left
            joint_color = (0, 255, 255)  # Yellow for joints
            
            # Finger tip indices for labeling
            finger_names = {8: 'Index', 12: 'Middle', 16: 'Ring', 20: 'Pinky', 4: 'Thumb'}
            
            # Draw each hand
            for hand_idx, landmarks in enumerate(landmarks_list):
                if not landmarks or len(landmarks) < 21:
                    continue
                
                skeleton_color = colors[hand_idx % len(colors)]
                hand_label = hand_positions[hand_idx]['label'] if hand_positions and hand_idx < len(hand_positions) else 'Hand'
                
                # Draw skeleton connections (bones) - skip if cv2 not available
                if cv2:
                    for conn in HAND_CONNECTIONS:
                        start_idx, end_idx = conn
                        if start_idx < len(landmarks) and end_idx < len(landmarks):
                            start_pt = landmarks[start_idx]
                            end_pt = landmarks[end_idx]
                            
                            # Convert normalized coordinates to pixel coordinates
                            start_x = int(start_pt['x'] * w)
                            start_y = int(start_pt['y'] * h)
                            end_x = int(end_pt['x'] * w)
                            end_y = int(end_pt['y'] * h)
                            
                            # Draw line (bone)
                            cv2.line(frame, (start_x, start_y), (end_x, end_y), skeleton_color, 2)
                    
                    # Draw joints (landmarks) as circles
                    for idx, landmark in enumerate(landmarks):
                        x = int(landmark['x'] * w)
                        y = int(landmark['y'] * h)
                        
                        # Palm is larger, fingers are smaller
                        radius = 6 if idx == 0 else 4
                        thickness = -1  # Filled circle
                        
                        cv2.circle(frame, (x, y), radius, joint_color, thickness)
                        
                        # Label important finger tips
                        if idx in finger_names:
                            text = finger_names[idx]
                            cv2.putText(frame, text, (x + 8, y - 5),
                                      cv2.FONT_HERSHEY_SIMPLEX, 0.4, joint_color, 1)
                    
                    # Add hand label at top
                    palm_x = int(landmarks[0]['x'] * w)
                    palm_y = int(landmarks[0]['y'] * h)
                    cv2.putText(frame, f"{hand_label} Hand", (palm_x - 40, palm_y - 30),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.6, skeleton_color, 2)
                    
                    # Draw detected sign label if available
                    if detected_signs and hand_idx < len(detected_signs):
                        sign = detected_signs[hand_idx]
                        cv2.putText(frame, f"Sign: {sign}", (palm_x - 40, palm_y - 10),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        
        except Exception as e:
            print(f"Error drawing hand landmarks: {e}")
        
        return frame
    
    def draw_finger_tracking_lines(self, frame: np.ndarray, landmarks_list: List[List[Dict]]) -> np.ndarray:
        """
        Draw enhanced finger tracking lines showing extension and direction
        Useful for understanding finger movements and states
        
        Args:
            frame: OpenCV image frame
            landmarks_list: List of hand landmarks
            
        Returns:
            Frame with finger tracking lines
        """
        if not landmarks_list:
            return frame
        
        try:
            h, w, c = frame.shape
            
            for hand_idx, landmarks in enumerate(landmarks_list):
                if not landmarks or len(landmarks) < 21:
                    continue
                
                # Colors for different fingers
                finger_colors = {
                    (2, 3, 4): (255, 0, 0),        # Thumb - Blue
                    (5, 6, 7, 8): (0, 255, 0),    # Index - Green
                    (9, 10, 11, 12): (255, 255, 0),  # Middle - Cyan
                    (13, 14, 15, 16): (255, 0, 255), # Ring - Magenta
                    (17, 18, 19, 20): (0, 255, 255)  # Pinky - Yellow
                }
                
                # Draw extended lines from base to tip for each finger - skip if cv2 not available
                if cv2:
                    for joint_indices, color in finger_colors.items():
                        if joint_indices[0] < len(landmarks) and joint_indices[-1] < len(landmarks):
                            base = landmarks[joint_indices[0]]
                            tip = landmarks[joint_indices[-1]]
                            
                            base_x = int(base['x'] * w)
                            base_y = int(base['y'] * h)
                            tip_x = int(tip['x'] * w)
                            tip_y = int(tip['y'] * h)
                            
                            # Draw extended line showing finger direction
                            cv2.line(frame, (base_x, base_y), (tip_x, tip_y), color, 2)
                            
                            # Extend line beyond tip to show direction
                            dx = tip_x - base_x
                            dy = tip_y - base_y
                            if dx != 0 or dy != 0:
                                length = np.sqrt(dx*dx + dy*dy)
                                if length > 0:
                                    # Direction vector
                                    dir_x = dx / length
                                    dir_y = dy / length
                                    # Extend 30 pixels beyond tip
                                    ext_x = int(tip_x + dir_x * 30)
                                    ext_y = int(tip_y + dir_y * 30)
                                    cv2.line(frame, (tip_x, tip_y), (ext_x, ext_y), color, 1)
                                    # Arrow head at end
                                    cv2.circle(frame, (ext_x, ext_y), 3, color, -1)
        
        except Exception as e:
            print(f"Error drawing finger tracking lines: {e}")
        
        return frame
    
    def add_hand_annotations(self, frame: np.ndarray, detection: Dict) -> np.ndarray:
        """
        Add annotations showing detected hand features and confidence
        
        Args:
            frame: OpenCV image frame  
            detection: Detection result from detect_signs()
            
        Returns:
            Frame with annotations
        """
        # Skip annotations if cv2 not available (headless environment)
        if not cv2:
            return frame
            
        try:
            h, w, c = frame.shape
            y_offset = 30
            
            # Display detection summary
            if detection['has_hands']:
                cv2.putText(frame, f"Hands Detected: {detection['hands_detected']}", 
                          (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                y_offset += 30
                
                cv2.putText(frame, f"Confidence: {detection['confidence']:.2%}", 
                          (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                y_offset += 30
                
                # Show detected signs
                if detection['gestures']:
                    for i, gesture in enumerate(detection['gestures'][:2]):  # Show up to 2 hands
                        cv2.putText(frame, f"Hand {i+1}: {gesture}", 
                                  (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
                        y_offset += 25
            else:
                cv2.putText(frame, "No hands detected", 
                          (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        
        except Exception as e:
            print(f"Error adding annotations: {e}")
        
        return frame

    def process_video_stream(self, video_source: str = 0) -> Generator[Dict, None, None]:
        """
        Process video stream from webcam or file
        Only yields frames with detected hands
        
        Args:
            video_source: 0 for webcam or path to video file
            
        Yields:
            Detected signs with timestamps (only when hands detected)
        """
        try:
            cap = cv2.VideoCapture(video_source)
            
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                
                detection = self.detect_signs(frame)
                
                # Only yield when hands are detected
                if detection['has_hands']:
                    yield detection
            
            cap.release()
            
        except Exception as e:
            print(f"Error processing video: {e}")
            raise


def initialize_detector(use_mock: bool = False) -> SignDetector:
    """
    Initialize sign detector with real MediaPipe hand tracking
    
    Args:
        use_mock: Legacy parameter (ignored - always uses real detector now)
        
    Returns:
        SignDetector instance with MediaPipe hand tracking
    """
    # Always use real hand tracking with MediaPipe
    return SignDetector(confidence_threshold=0.7)
